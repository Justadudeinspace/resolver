from aiogram.fsm.state import State, StatesGroup


class Flow(StatesGroup):
    waiting_for_text = State()
    waiting_for_feedback = State()
