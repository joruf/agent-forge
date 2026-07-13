"""Human-in-the-loop approval queue."""

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from agentforge.models.schemas import (
    ApprovalRequest,
    ApprovalResponse,
    ApprovalResumeState,
)


class ApprovalManager:
    """Manages pending user approvals for sensitive actions."""

    def __init__(self) -> None:
        """Initialize approval manager."""
        self._pending: dict[str, ApprovalRequest] = {}
        self._futures: dict[str, asyncio.Future] = {}
        self._resume_states: dict[str, ApprovalResumeState] = {}

    async def request(
        self,
        chat_id: str,
        action_type: str,
        description: str,
        payload: dict[str, Any],
    ) -> str:
        """
        Create approval request and wait for user response.

        :return: Approval request ID
        """
        approval_id = str(uuid.uuid4())
        request = ApprovalRequest(
            id=approval_id,
            chat_id=chat_id,
            action_type=action_type,
            description=description,
            payload=payload,
            created_at=datetime.now(timezone.utc),
        )
        self._pending[approval_id] = request
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self._futures[approval_id] = future
        return approval_id

    def list_pending(self, chat_id: str | None = None) -> list[ApprovalRequest]:
        """List pending approvals, optionally filtered by chat."""
        items = list(self._pending.values())
        if chat_id:
            items = [a for a in items if a.chat_id == chat_id]
        return items

    async def respond(self, approval_id: str, response: ApprovalResponse) -> bool:
        """
        Process user approval response.

        :return: True if approval was found and processed
        """
        if approval_id not in self._pending:
            return False
        future = self._futures.get(approval_id)
        del self._pending[approval_id]
        if future and not future.done():
            future.set_result(response)
        return True

    async def wait_for_response(
        self,
        approval_id: str,
        timeout: float = 300.0,
    ) -> ApprovalResponse | None:
        """Wait for user to approve or deny."""
        future = self._futures.get(approval_id)
        if not future:
            return None
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(approval_id, None)
            return None
        finally:
            self._futures.pop(approval_id, None)

    def set_resume_state(
        self,
        approval_id: str,
        state: dict[str, Any] | ApprovalResumeState,
    ) -> None:
        """
        Store orchestration continuation state for an approval request.

        :param approval_id: Approval request identifier
        :param state: Serializable continuation state
        """
        if isinstance(state, ApprovalResumeState):
            self._resume_states[approval_id] = state
            return
        self._resume_states[approval_id] = ApprovalResumeState.model_validate(state)

    def pop_resume_state(self, approval_id: str) -> ApprovalResumeState | None:
        """
        Remove and return continuation state for an approval request.

        :param approval_id: Approval request identifier
        :return: Stored continuation state, if available
        """
        state = self._resume_states.pop(approval_id, None)
        if state is None:
            return None
        if isinstance(state, ApprovalResumeState):
            return state
        try:
            return ApprovalResumeState.model_validate(state)
        except Exception as exc:
            raise ValueError(f"Invalid resume state for approval '{approval_id}'.") from exc


approval_manager = ApprovalManager()
