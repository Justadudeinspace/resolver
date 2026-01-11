from aiogram.fsm.state import State, StatesGroup


class Flow(StatesGroup):
    waiting_for_text = State()
    waiting_for_feedback = State()
    waiting_for_group_rag = State()
    waiting_for_welcome_message = State()
    waiting_for_rules_text = State()
    waiting_for_security_value = State()
