"""Tests for order state machine and transitions."""

import pytest

from bot.layer3_execution.order_state import (
    InvalidTransitionError,
    OrderStateMachine,
)
from bot.models import OrderState


class TestOrderStateMachine:
    def test_initial_state_is_pending(self):
        fsm = OrderStateMachine()
        assert fsm.state == OrderState.PENDING

    def test_custom_initial_state(self):
        fsm = OrderStateMachine(OrderState.SUBMITTED)
        assert fsm.state == OrderState.SUBMITTED

    def test_valid_transition_pending_to_submitted(self):
        fsm = OrderStateMachine()
        fsm.transition(OrderState.SUBMITTED)
        assert fsm.state == OrderState.SUBMITTED

    def test_valid_transition_submitted_to_filled(self):
        fsm = OrderStateMachine(OrderState.SUBMITTED)
        fsm.transition(OrderState.FILLED)
        assert fsm.state == OrderState.FILLED

    def test_valid_transition_filled_to_closed(self):
        fsm = OrderStateMachine(OrderState.FILLED)
        fsm.transition(OrderState.CLOSED)
        assert fsm.state == OrderState.CLOSED

    def test_valid_transition_submitted_to_partially_filled(self):
        fsm = OrderStateMachine(OrderState.SUBMITTED)
        fsm.transition(OrderState.PARTIALLY_FILLED)
        assert fsm.state == OrderState.PARTIALLY_FILLED

    def test_valid_transition_partial_to_filled(self):
        fsm = OrderStateMachine(OrderState.PARTIALLY_FILLED)
        fsm.transition(OrderState.FILLED)
        assert fsm.state == OrderState.FILLED

    def test_valid_transition_submitted_to_rejected(self):
        fsm = OrderStateMachine(OrderState.SUBMITTED)
        fsm.transition(OrderState.REJECTED)
        assert fsm.state == OrderState.REJECTED

    def test_valid_transition_error_to_pending(self):
        fsm = OrderStateMachine(OrderState.ERROR)
        fsm.transition(OrderState.PENDING)
        assert fsm.state == OrderState.PENDING

    def test_invalid_transition_raises(self):
        fsm = OrderStateMachine()
        with pytest.raises(InvalidTransitionError):
            fsm.transition(OrderState.FILLED)  # Can't go from PENDING to FILLED

    def test_invalid_transition_from_terminal(self):
        fsm = OrderStateMachine(OrderState.CLOSED)
        with pytest.raises(InvalidTransitionError):
            fsm.transition(OrderState.PENDING)

    def test_closed_is_terminal(self):
        fsm = OrderStateMachine(OrderState.CLOSED)
        assert fsm.is_terminal is True
        assert fsm.is_active is False

    def test_cancelled_is_terminal(self):
        fsm = OrderStateMachine(OrderState.CANCELLED)
        assert fsm.is_terminal is True

    def test_rejected_is_terminal(self):
        fsm = OrderStateMachine(OrderState.REJECTED)
        assert fsm.is_terminal is True

    def test_pending_is_active(self):
        fsm = OrderStateMachine()
        assert fsm.is_active is True
        assert fsm.is_terminal is False

    def test_can_transition_returns_true(self):
        fsm = OrderStateMachine()
        assert fsm.can_transition(OrderState.SUBMITTED) is True

    def test_can_transition_returns_false(self):
        fsm = OrderStateMachine()
        assert fsm.can_transition(OrderState.CLOSED) is False

    def test_valid_transitions_from_submitted(self):
        fsm = OrderStateMachine(OrderState.SUBMITTED)
        valid = fsm.valid_transitions()
        assert OrderState.FILLED in valid
        assert OrderState.PARTIALLY_FILLED in valid
        assert OrderState.CANCELLED in valid
        assert OrderState.REJECTED in valid
        assert OrderState.ERROR in valid

    def test_full_happy_path(self):
        """Test the complete lifecycle: PENDING -> SUBMITTED -> FILLED -> CLOSED."""
        fsm = OrderStateMachine()
        assert fsm.state == OrderState.PENDING

        fsm.transition(OrderState.SUBMITTED)
        assert fsm.state == OrderState.SUBMITTED

        fsm.transition(OrderState.FILLED)
        assert fsm.state == OrderState.FILLED

        fsm.transition(OrderState.CLOSED)
        assert fsm.state == OrderState.CLOSED
        assert fsm.is_terminal is True

    def test_partial_fill_path(self):
        """Test lifecycle with partial fills."""
        fsm = OrderStateMachine()
        fsm.transition(OrderState.SUBMITTED)
        fsm.transition(OrderState.PARTIALLY_FILLED)
        fsm.transition(OrderState.FILLED)
        fsm.transition(OrderState.CLOSED)
        assert fsm.is_terminal is True

    def test_error_recovery_path(self):
        """Test error -> retry -> success path."""
        fsm = OrderStateMachine()
        fsm.transition(OrderState.ERROR)
        assert fsm.state == OrderState.ERROR

        # Retry
        fsm.transition(OrderState.PENDING)
        fsm.transition(OrderState.SUBMITTED)
        fsm.transition(OrderState.FILLED)
        assert fsm.state == OrderState.FILLED
