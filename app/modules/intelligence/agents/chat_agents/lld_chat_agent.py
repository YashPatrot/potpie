import enum
import json
import logging
import time
from functools import lru_cache
from typing import AsyncGenerator, Dict, List

from langchain.schema import HumanMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import (
    ChatPromptTemplate,
    HumanMessagePromptTemplate,
    MessagesPlaceholder,
    SystemMessagePromptTemplate,
)
from langchain_core.runnables import RunnableSequence
from langgraph.graph import END, START, StateGraph
from langgraph.types import StreamWriter
from sqlalchemy.orm import Session
from typing_extensions import TypedDict

from app.modules.conversations.message.message_model import MessageType
from app.modules.conversations.message.message_schema import NodeContext
from app.modules.intelligence.agents.agents.rag_agent import kickoff_rag_agent
from app.modules.intelligence.agents.agents_service import AgentsService
from app.modules.intelligence.llm_provider.llm_provider_service import (
    LLMProviderService,
)
from app.modules.intelligence.memory.chat_history_service import ChatHistoryService
from app.modules.intelligence.prompts.prompt_schema import PromptResponse, PromptType
from app.modules.intelligence.prompts.prompt_service import PromptService
from app.modules.intelligence.prompts_provider.agent_types import SystemAgentType
from app.modules.intelligence.prompts_provider.classification_types import (
    ClassificationResponse,
    ClassificationResult,
)

logger = logging.getLogger(__name__)


class LLDChatAgent:
    def __init__(self, mini_llm, llm, db: Session):
        self.mini_llm = mini_llm
        self.llm = llm
        self.history_manager = ChatHistoryService(db)
        self.prompt_service = PromptService(db)
        self.agents_service = AgentsService(db)
        self.chain = None
        self.db = db

    @lru_cache(maxsize=2)
    async def _get_prompts(self, user_id: str) -> Dict[PromptType, PromptResponse]:
        llm_provider_service = LLMProviderService.create(self.db, user_id)
        preferred_llm, _ = await llm_provider_service.get_preferred_llm(user_id)
        prompts = await self.prompt_service.get_prompts(
            "QNA_AGENT", [PromptType.SYSTEM, PromptType.HUMAN], preferred_llm
        )
        return {
            (
                prompt.type.value if isinstance(prompt.type, enum.Enum) else prompt.type
            ): prompt
            for prompt in prompts
        }

    async def _create_chain(self, user_id: str) -> RunnableSequence:
        prompts = await self._get_prompts(user_id)
        system_prompt = prompts.get(PromptType.SYSTEM.value)
        human_prompt = prompts.get(PromptType.HUMAN.value)

        if not system_prompt or not human_prompt:
            raise ValueError("Required prompts not found for QNA_AGENT")

        prompt_template = ChatPromptTemplate(
            messages=[
                SystemMessagePromptTemplate.from_template(system_prompt.text),
                MessagesPlaceholder(variable_name="history"),
                MessagesPlaceholder(variable_name="tool_results"),
                HumanMessagePromptTemplate.from_template(human_prompt.text),
            ]
        )
        return prompt_template | self.mini_llm

    async def _classify_query(
        self, query: str, history: List[HumanMessage], user_id: str
    ):
        llm_provider_service = LLMProviderService.create(self.db, user_id)
        preferred_llm, _ = await llm_provider_service.get_preferred_llm(user_id)
        prompt = await self.prompt_service.get_prompts(
            SystemAgentType.LLD, [PromptType.SYSTEM], preferred_llm
        )
        inputs = {"query": query, "history": [msg.content for msg in history[-10:]]}

        parser = PydanticOutputParser(pydantic_object=ClassificationResponse)
        prompt_with_parser = ChatPromptTemplate.from_template(
            template=prompt,
            partial_variables={"format_instructions": parser.get_format_instructions()},
        )
        chain = prompt_with_parser | self.llm | parser
        response = await chain.ainvoke(input=inputs)

        return response.classification

    class State(TypedDict):
        query: str
        project_id: str
        user_id: str
        conversation_id: str
        node_ids: List[NodeContext]

    async def _stream_rag_agent(self, state: State, writer: StreamWriter):
        async for chunk in self.execute(
            state["query"],
            state["project_id"],
            state["user_id"],
            state["conversation_id"],
            state["node_ids"],
        ):
            writer(chunk)

    def _create_graph(self):
        graph_builder = StateGraph(LLDChatAgent.State)
        graph_builder.add_node("rag_agent", self._stream_rag_agent)
        graph_builder.add_edge(START, "rag_agent")
        graph_builder.add_edge("rag_agent", END)
        return graph_builder.compile()

    async def run(
        self,
        query: str,
        project_id: str,
        user_id: str,
        conversation_id: str,
        node_ids: List[NodeContext],
    ):
        state = {
            "query": query,
            "project_id": project_id,
            "user_id": user_id,
            "conversation_id": conversation_id,
            "node_ids": node_ids,
        }
        graph = self._create_graph()
        async for chunk in graph.astream(state, stream_mode="custom"):
            yield chunk

    async def execute(
        self,
        query: str,
        project_id: str,
        user_id: str,
        conversation_id: str,
        node_ids: List[NodeContext],
    ) -> AsyncGenerator[str, None]:
        start_time = time.time()  # Start the timer
        try:
            if not self.chain:
                self.chain = await self._create_chain(user_id)

            history = self.history_manager.get_session_history(user_id, conversation_id)
            validated_history = [
                (
                    HumanMessage(content=str(msg))
                    if isinstance(msg, (str, int, float))
                    else msg
                )
                for msg in history
            ]

            classification_start_time = time.time()  # Start timer for classification
            classification = await self._classify_query(
                query, validated_history, user_id
            )
            classification_duration = (
                time.time() - classification_start_time
            )  # Calculate duration
            logger.info(
                f"Time elapsed since entering run: {time.time() - start_time:.2f}s, "
                f"Duration of classify method call: {classification_duration:.2f}s"
            )

            tool_results = []
            citations = []
            if classification == ClassificationResult.AGENT_REQUIRED:
                async for chunk in kickoff_rag_agent(
                    query,
                    project_id,
                    [
                        msg.content
                        for msg in validated_history
                        if isinstance(msg, HumanMessage)
                    ],
                    node_ids,
                    self.db,
                    self.llm,
                    self.mini_llm,
                    user_id,
                ):
                    content = str(chunk)
                    self.history_manager.add_message_chunk(
                        conversation_id,
                        content,
                        MessageType.AI_GENERATED,
                        citations=citations,
                    )
                    yield json.dumps(
                        {
                            "citations": citations,
                            "message": content,
                        }
                    )

                self.history_manager.flush_message_buffer(
                    conversation_id, MessageType.AI_GENERATED
                )

            if classification != ClassificationResult.AGENT_REQUIRED:
                inputs = {
                    "history": validated_history[-10:],
                    "tool_results": tool_results,
                    "input": query,
                }

                logger.debug(f"Inputs to LLM: {inputs}")
                citations = self.agents_service.format_citations(citations)
                full_response = ""
                add_stream_chunk_start_time = (
                    time.time()
                )  # Start timer for adding message chunk

                async for chunk in self.chain.astream(inputs):
                    content = chunk.content if hasattr(chunk, "content") else str(chunk)
                    full_response += content

                    self.history_manager.add_message_chunk(
                        conversation_id,
                        content,
                        MessageType.AI_GENERATED,
                        citations=citations,
                    )
                    yield json.dumps(
                        {
                            "citations": citations,
                            "message": content,
                        }
                    )
                add_stream_chunk_duration = (
                    time.time() - add_stream_chunk_start_time
                )  # Calculate duration
                logger.info(
                    f"Time elapsed since entering run: {time.time() - start_time:.2f}s, "
                    f"Duration of adding message chunk during streaming: {add_stream_chunk_duration:.2f}s"
                )

                flush_stream_buffer_start_time = (
                    time.time()
                )  # Start timer for flushing message buffer after streaming
                self.history_manager.flush_message_buffer(
                    conversation_id, MessageType.AI_GENERATED
                )
                flush_stream_buffer_duration = (
                    time.time() - flush_stream_buffer_start_time
                )  # Calculate duration
                logger.info(
                    f"Time elapsed since entering run: {time.time() - start_time:.2f}s, "
                    f"Duration of flushing message buffer after streaming: {flush_stream_buffer_duration:.2f}s"
                )

        except Exception as e:
            logger.error(f"Error during LLDChatAgent run: {str(e)}", exc_info=True)
            yield f"An error occurred: {str(e)}"
