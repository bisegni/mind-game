import unittest

from mind_game.engine import (
    BaseReActEngine,
    ReActDecision,
    SubagentTask,
    ToolCall,
)


class ScriptedReasoner:
    def __init__(self, decisions):
        self._decisions = list(decisions)
        self.snapshots = []

    def decide(self, snapshot, tools):
        self.snapshots.append(snapshot)
        if not self._decisions:
            raise AssertionError("reasoner was asked for more decisions than expected")
        return self._decisions.pop(0)


class RecordingSubagentRunner:
    def __init__(self):
        self.tasks = []

    def run(self, task: SubagentTask) -> str:
        self.tasks.append(task)
        return f"{task.role}:{task.task}"


class EngineTests(unittest.TestCase):
    def test_run_turn_dispatches_tools_then_returns_final_reply(self) -> None:
        reasoner = ScriptedReasoner(
            [
                ReActDecision(
                    kind="tool",
                    tool=ToolCall(
                        name="session.write_fact",
                        arguments={"key": "tone", "value": "playful"},
                    ),
                ),
                ReActDecision(
                    kind="tool",
                    tool=ToolCall(
                        name="subagent.delegate",
                        arguments={
                            "role": "summarizer",
                            "task": "compress the current state",
                        },
                    ),
                ),
                ReActDecision(kind="final", content="Ready for the next move."),
            ],
        )
        runner = RecordingSubagentRunner()
        engine = BaseReActEngine(reasoner, subagent_runner=runner)

        turn = engine.run_turn("hello there")

        self.assertEqual(turn.reply, "Ready for the next move.")
        self.assertEqual(engine.session.turn, 1)
        self.assertEqual(engine.session.facts["tone"], "playful")
        self.assertEqual(len(runner.tasks), 1)
        self.assertEqual(runner.tasks[0].role, "summarizer")
        self.assertEqual(runner.tasks[0].task, "compress the current state")
        self.assertEqual(runner.tasks[0].context["player_input"], "hello there")
        self.assertEqual(len(turn.observations), 2)
        self.assertEqual(reasoner.snapshots[1]["observations"][0]["tool"], "session.write_fact")
        self.assertEqual(reasoner.snapshots[2]["observations"][1]["tool"], "subagent.delegate")

    def test_tools_are_exposed_as_named_abstractions(self) -> None:
        reasoner = ScriptedReasoner([ReActDecision(kind="final", content="done")])
        engine = BaseReActEngine(reasoner)

        tool_names = [tool.name for tool in engine.tools]

        self.assertIn("session.read", tool_names)
        self.assertIn("session.write_fact", tool_names)
        self.assertIn("session.add_note", tool_names)
        self.assertIn("subagent.delegate", tool_names)


if __name__ == "__main__":
    unittest.main()
