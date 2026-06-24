"""v2 schema tests — enums, transitions, dispatch keys, rosters, expected sets."""

import unittest

from v2.schema import (
    WorkflowStatus,
    TERMINAL_STATES,
    WAITING_STATES,
    requires_transition,
    can_transition,
    dispatch_key,
    proposal_path,
    comment_path,
    walkthrough_path,
    review_path,
    instruction_path,
    build_roster,
    planner_seats,
    planner_agents,
    planner_count,
    all_agents,
    make_manifest,
    set_status,
    increment_review_iteration,
    expected_proposals,
    expected_comments,
    APPROVE,
    REQUEST_CHANGES,
    CANCEL,
)


class TestWorkflowStatus(unittest.TestCase):

    def test_terminal_states(self):
        self.assertIn(WorkflowStatus.COMPLETED, TERMINAL_STATES)
        self.assertIn(WorkflowStatus.CANCELLED, TERMINAL_STATES)
        self.assertIn(WorkflowStatus.FAILED, TERMINAL_STATES)

    def test_waiting_states(self):
        self.assertIn(WorkflowStatus.WAITING_USER_CLARIFICATION, WAITING_STATES)
        self.assertIn(WorkflowStatus.WAITING_USER_APPROVAL, WAITING_STATES)
        self.assertIn(WorkflowStatus.WAITING_USER_DECISION, WAITING_STATES)

    def test_cannot_transition_from_terminal(self):
        for s in TERMINAL_STATES:
            self.assertFalse(can_transition(s, WorkflowStatus.CHAIR_CLARIFYING))

    def test_cancel_from_any_nonterminal(self):
        for s in WorkflowStatus:
            if s not in TERMINAL_STATES:
                self.assertTrue(can_transition(s, WorkflowStatus.CANCELLED), f"{s} -> CANCELLED")

    def test_fail_from_any_nonterminal(self):
        for s in WorkflowStatus:
            if s not in TERMINAL_STATES:
                self.assertTrue(can_transition(s, WorkflowStatus.FAILED), f"{s} -> FAILED")

    def test_happy_path_transitions(self):
        path = [
            WorkflowStatus.CREATED,
            WorkflowStatus.CHAIR_CLARIFYING,
            WorkflowStatus.PLANNING,
            WorkflowStatus.PEER_REVIEW,
            WorkflowStatus.CHAIR_SYNTHESIS,
            WorkflowStatus.WAITING_USER_APPROVAL,
            WorkflowStatus.EXECUTING,
            WorkflowStatus.REVIEWING,
            WorkflowStatus.COMMITTING,
            WorkflowStatus.CHAIR_FINAL_CHECK,
            WorkflowStatus.COMPLETED,
        ]
        for i in range(len(path) - 1):
            self.assertTrue(
                can_transition(path[i], path[i + 1]),
                f"{path[i].value} -> {path[i + 1].value}",
            )

    def test_requires_transition_raises_on_illegal(self):
        with self.assertRaises(ValueError):
            requires_transition(WorkflowStatus.CREATED, WorkflowStatus.COMPLETED)


class TestDispatchKeys(unittest.TestCase):

    def test_dispatch_key_format(self):
        key = dispatch_key("wf_001", "proposal", "planner_a", 1)
        self.assertEqual(key, "wf_001:proposal:planner_a:1")

    def test_dispatch_key_unique_per_iteration(self):
        k1 = dispatch_key("wf_001", "review", "reviewer", 1)
        k2 = dispatch_key("wf_001", "review", "reviewer", 2)
        self.assertNotEqual(k1, k2)


class TestArtifactPaths(unittest.TestCase):

    def test_proposal_path(self):
        self.assertEqual(proposal_path("planner_a"), "proposals/planner_a.md")

    def test_comment_path(self):
        self.assertEqual(comment_path("planner_a", "planner_b"), "comments/planner_a_on_planner_b.md")

    def test_walkthrough_path(self):
        self.assertEqual(walkthrough_path(1), "walkthroughs/walkthrough_001.md")
        self.assertEqual(walkthrough_path(42), "walkthroughs/walkthrough_042.md")

    def test_review_path(self):
        self.assertEqual(review_path(2), "reviews/review_002.md")

    def test_instruction_path(self):
        self.assertEqual(instruction_path("chair"), "instructions/chair.md")


class TestRoster(unittest.TestCase):

    def test_build_roster(self):
        roster = build_roster(
            chair="codex",
            planners=[
                {"seat": "planner_a", "agent": "antigravity"},
                {"seat": "planner_b", "agent": "grok_build"},
                {"seat": "planner_c", "agent": "codex"},
            ],
            executor="antigravity",
            reviewer="grok_build",
        )
        self.assertEqual(roster["chair"], "codex")
        self.assertEqual(len(roster["planners"]), 3)
        self.assertEqual(roster["executor"], "antigravity")
        self.assertEqual(roster["reviewer"], "grok_build")

    def test_planner_seats(self):
        roster = build_roster("a", [{"seat": "planner_a", "agent": "x"}, {"seat": "planner_b", "agent": "y"}], "e", "r")
        self.assertEqual(planner_seats(roster), ["planner_a", "planner_b"])

    def test_planner_agents(self):
        roster = build_roster("a", [{"seat": "planner_a", "agent": "x"}, {"seat": "planner_b", "agent": "y"}], "e", "r")
        self.assertEqual(planner_agents(roster), ["x", "y"])

    def test_planner_count(self):
        roster = build_roster("a", [{"seat": "a", "agent": "x"}], "e", "r")
        self.assertEqual(planner_count(roster), 1)

    def test_all_agents_deduplicates(self):
        roster = build_roster(
            chair="codex",
            planners=[{"seat": "a", "agent": "codex"}],
            executor="codex",
            reviewer="codex",
        )
        self.assertEqual(all_agents(roster), ["codex"])

    def test_all_agents_multiple(self):
        roster = build_roster(
            chair="c",
            planners=[{"seat": "a", "agent": "x"}, {"seat": "b", "agent": "y"}],
            executor="z",
            reviewer="w",
        )
        self.assertEqual(all_agents(roster), ["c", "w", "x", "y", "z"])


class TestManifest(unittest.TestCase):

    def test_make_manifest(self):
        roster = build_roster("c", [{"seat": "a", "agent": "x"}], "e", "r")
        m = make_manifest("wf_001", "/tmp/test", roster)
        self.assertEqual(m["schema_version"], 1)
        self.assertEqual(m["workflow_id"], "wf_001")
        self.assertEqual(m["status"], "CREATED")
        self.assertEqual(m["roster"]["chair"], "c")
        self.assertTrue(m["require_user_plan_approval"])

    def test_set_status(self):
        roster = build_roster("c", [], "e", "r")
        m = make_manifest("wf_001", "/tmp/test", roster)
        set_status(m, WorkflowStatus.PLANNING)
        self.assertEqual(m["status"], "PLANNING")

    def test_increment_review_iteration(self):
        roster = build_roster("c", [], "e", "r")
        m = make_manifest("wf_001", "/tmp/test", roster)
        self.assertEqual(increment_review_iteration(m), 1)
        self.assertEqual(increment_review_iteration(m), 2)


class TestExpectedSets(unittest.TestCase):

    def test_expected_proposals_1(self):
        roster = build_roster("c", [{"seat": "planner_a", "agent": "x"}], "e", "r")
        self.assertEqual(expected_proposals(roster), ["proposals/planner_a.md"])

    def test_expected_proposals_3(self):
        roster = build_roster("c", [
            {"seat": "planner_a", "agent": "x"},
            {"seat": "planner_b", "agent": "y"},
            {"seat": "planner_c", "agent": "z"},
        ], "e", "r")
        self.assertEqual(
            expected_proposals(roster),
            ["proposals/planner_a.md", "proposals/planner_b.md", "proposals/planner_c.md"],
        )

    def test_expected_comments_1(self):
        roster = build_roster("c", [{"seat": "planner_a", "agent": "x"}], "e", "r")
        self.assertEqual(expected_comments(roster), [])

    def test_expected_comments_2(self):
        roster = build_roster("c", [
            {"seat": "planner_a", "agent": "x"},
            {"seat": "planner_b", "agent": "y"},
        ], "e", "r")
        comments = expected_comments(roster)
        self.assertEqual(len(comments), 2)
        self.assertIn("comments/planner_a_on_planner_b.md", comments)
        self.assertIn("comments/planner_b_on_planner_a.md", comments)

    def test_expected_comments_3(self):
        roster = build_roster("c", [
            {"seat": "planner_a", "agent": "x"},
            {"seat": "planner_b", "agent": "y"},
            {"seat": "planner_c", "agent": "z"},
        ], "e", "r")
        comments = expected_comments(roster)
        self.assertEqual(len(comments), 6)


class TestDecisionTokens(unittest.TestCase):

    def test_constants(self):
        self.assertEqual(APPROVE, "APPROVE")
        self.assertEqual(REQUEST_CHANGES, "REQUEST_CHANGES")
        self.assertEqual(CANCEL, "CANCEL")


if __name__ == "__main__":
    unittest.main()
