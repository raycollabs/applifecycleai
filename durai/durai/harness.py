"""LangGraph adapter with PostgreSQL checkpoints and explicit review boundaries."""
from contextlib import contextmanager
from typing import TypedDict, Protocol
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import sql
from .db import connect
from .coordination import Coordination


class AgentHarness(Protocol):
    def invoke(self, value, config): ...


class TradingState(TypedDict, total=False):
    run_id: str
    stage: str
    shares: int


def prepare_checkpoints(database):
    # Third-party DDL is an operator step, never a runtime privilege.
    with connect('postgres', database, autocommit=True) as connection:
        connection.execute('SET search_path=durai_checkpoints')
        saver = PostgresSaver(connection)
        saver.setup()
        connection.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA durai_checkpoints TO durai_runtime')


@contextmanager
def trading_harness(database='durai_local'):
    coordinator = Coordination(database)

    def prepare(state):
        coordinator.run(state['run_id'])
        coordinator.checkpoint(state['run_id'], {'stage': 'PREPARED'})
        return {'stage': 'PREPARED'}

    def finish(state):
        result = coordinator.book_once('harness:' + state['run_id'], 'H-' + state['run_id'],
                                       2, 100, 'repaired-v3')
        coordinator.checkpoint(state['run_id'], {'stage': 'BOOKED', 'shares': result['shares']}, 'SUCCEEDED')
        return {'stage': 'BOOKED', 'shares': result['shares']}

    with connect('durai_runtime', database, autocommit=True) as connection:
        connection.execute('SET search_path=durai_checkpoints')
        saver = PostgresSaver(connection)
        builder = StateGraph(TradingState)
        builder.add_node('prepare', prepare)
        builder.add_node('finish', finish)
        builder.add_edge(START, 'prepare')
        builder.add_edge('prepare', 'finish')
        builder.add_edge('finish', END)
        yield builder.compile(checkpointer=saver, interrupt_before=['finish'])
