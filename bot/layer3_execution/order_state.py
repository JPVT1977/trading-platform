from __future__ import annotations

from bot.models import OrderState


# Valid state transitions — defines the order lifecycle FSM
TRANSITIONS: dict[OrderState, set[OrderState]] = {
    OrderState.PENDING: {
        OrderState.SUBMITTED,
        OrderState.CANCELLED,
        OrderState.ERROR,
    },
    OrderState.SUBMITTED: {
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.CANCELLED,
        OrderState.REJECTED,
        OrderState.ERROR,
    },
    OrderState.PARTIALLY_FILLED: {
        OrderState.FILLED,
        OrderState.CANCELLED,
        OrderState.ERROR,
    },
    OrderState.FILLED: {
        OrderState.CLOSED,
    },
    OrderState.CANCELLED: set(),   # Terminal state
    OrderState.REJECTED: set(),    # Terminal state
    OrderState.CLOSED: set(),      # Terminal state
    OrderState.ERROR: {
        OrderState.PENDING,        # Can retry from error
    },
}


class InvalidTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""


class OrderStateMachine:
    """Finite State Machine for order lifecycle management.

    Prevents invalid transitions and provides introspection on terminal states.
    """

    def __init__(self, initial_state: OrderState = OrderState.PENDING) -> None:
        self._state = initial_state

    @property
    def state(self) -> OrderState:
        return self._state

    @property
    def is_terminal(self) -> bool:
        """Whether the order is in a terminal (no further transitions) state."""
        return len(TRANSITIONS.get(self._state, set())) == 0

    @property
    def is_active(self) -> bool:
        """Whether the order is in an active (non-terminal) state."""
        return not self.is_terminal

    def valid_transitions(self) -> set[OrderState]:
        """Return the set of states this order can transition to."""
        return TRANSITIONS.get(self._state, set())

    def can_transition(self, target: OrderState) -> bool:
        """Check if a transition to the target state is valid."""
        return target in TRANSITIONS.get(self._state, set())

    def transition(self, target: OrderState) -> None:
        """Execute a state transition. Raises InvalidTransitionError if invalid."""
        if not self.can_transition(target):
            valid = TRANSITIONS.get(self._state, set())
            raise InvalidTransitionError(
                f"Cannot transition from {self._state.value} to {target.value}. "
                f"Valid transitions: {{{', '.join(s.value for s in valid)}}}"
            )
        self._state = target
