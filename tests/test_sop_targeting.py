from __future__ import annotations

import unittest

from cad_ai.sop_knowledge.targeting import resolve


def make_steps() -> list[dict[str, object]]:
    return [
        {
            "id": 3,
            "step_code": "HD-03",
            "sequence_no": 3,
            "title": "裁线与长度补偿",
            "action": "按要求裁切线材并预留补偿长度",
            "method_json": ["核对长度", "裁切线材"],
            "exception_json": ["长度不符时隔离线材"],
        },
        {
            "id": 11,
            "step_code": "HD-11",
            "sequence_no": 11,
            "title": "电气性能检验",
            "action": "执行电气性能测试",
            "quality_check_json": ["检查导通与绝缘结果"],
        },
        {
            "id": 14,
            "step_code": "HD-14",
            "sequence_no": 14,
            "title": "成品外观检验",
            "action": "检查成品外观",
            "quality_check_json": ["核对外观状态"],
        },
        {
            "id": 15,
            "step_code": "HD-15",
            "sequence_no": 15,
            "title": "记录复核、异常隔离与人工放行闸门",
            "action": "复核记录并隔离异常品",
            "method_json": ["登记工单号和异常现象", "隔离异常品"],
            "exception_json": ["异常未关闭时不得放行"],
        },
    ]


def context_for(step_id: int) -> list[dict[str, object]]:
    return [{
        "id": 25,
        "role": "assistant",
        "content": "已完成上一项修改。",
        "metadata_json": {
            "target_resolution": {
                "status": "resolved",
                "selected_step_id": step_id,
                "candidates": [],
            }
        },
    }]


class SopTargetingTests(unittest.TestCase):
    def test_explicit_new_wording_beats_previous_context(self) -> None:
        result = resolve(
            "把隔离步骤的安全要求补充为：操作前确认设备状态。",
            make_steps(),
            context_for(3),
        )

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.selected_step_id, 15)
        self.assertFalse(result.used_context)

    def test_negative_wording_excludes_the_rejected_candidate(self) -> None:
        result = resolve(
            "不是裁线，是隔离，把安全要求补充为操作前确认设备状态。",
            make_steps(),
            context_for(3),
        )

        self.assertEqual(result.selected_step_id, 15)
        self.assertIn(3, result.excluded_step_ids)

    def test_pronoun_can_inherit_recent_resolved_target(self) -> None:
        result = resolve("把它的安全要求也补充完整。", make_steps(), context_for(15))

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.selected_step_id, 15)
        self.assertTrue(result.used_context)

    def test_generic_title_returns_candidates_instead_of_guessing(self) -> None:
        result = resolve("把检验工序的记录要求补充完整。", make_steps(), [])

        self.assertEqual(result.status, "needs_choice")
        self.assertIsNone(result.selected_step_id)
        self.assertEqual([item.step_id for item in result.candidates[:2]], [11, 14])

    def test_pending_candidate_can_be_selected_by_ordinal_language(self) -> None:
        history = [{
            "id": 42,
            "role": "assistant",
            "content": "请先选择工序。",
            "metadata_json": {
                "pending_instruction": "把检验工序的记录要求补充完整。",
                "target_resolution": {
                    "status": "needs_choice",
                    "candidates": [
                        {"step_id": 11, "step_code": "HD-11", "sequence_no": 11, "title": "电气性能检验", "reason": "名称包含检验"},
                        {"step_id": 14, "step_code": "HD-14", "sequence_no": 14, "title": "成品外观检验", "reason": "名称包含检验"},
                    ],
                },
            },
        }]

        result = resolve("第二个", make_steps(), history)

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.selected_step_id, 14)
        self.assertEqual(result.effective_instruction, "把检验工序的记录要求补充完整。")
        self.assertEqual(result.pending_message_id, 42)

    def test_clicked_candidate_must_belong_to_pending_candidate_set(self) -> None:
        history = [{
            "id": 42,
            "role": "assistant",
            "content": "请先选择工序。",
            "metadata_json": {
                "pending_instruction": "把检验工序的记录要求补充完整。",
                "target_resolution": {
                    "status": "needs_choice",
                    "candidates": [
                        {"step_id": 11, "step_code": "HD-11", "sequence_no": 11, "title": "电气性能检验", "reason": "名称包含检验"},
                        {"step_id": 14, "step_code": "HD-14", "sequence_no": 14, "title": "成品外观检验", "reason": "名称包含检验"},
                    ],
                },
            },
        }]

        result = resolve(
            "选择候选工序",
            make_steps(),
            history,
            selected_step_id=3,
            pending_message_id=42,
        )

        self.assertEqual(result.status, "not_found")
        self.assertIsNone(result.selected_step_id)

    def test_existing_step_field_addition_is_not_mistaken_for_a_new_step(self) -> None:
        result = resolve(
            "增加裁线工序的安全要求：操作前检查刀具。",
            make_steps(),
            [],
        )

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.selected_step_id, 3)

    def test_resolved_pending_candidate_cannot_be_submitted_again(self) -> None:
        history = [{
            "id": 42,
            "role": "assistant",
            "content": "请先选择工序。",
            "metadata_json": {
                "pending_instruction": "把检验工序的记录要求补充完整。",
                "target_resolution": {
                    "status": "needs_choice",
                    "candidates": [
                        {"step_id": 11, "step_code": "HD-11", "sequence_no": 11, "title": "电气性能检验", "reason": "名称包含检验"},
                        {"step_id": 14, "step_code": "HD-14", "sequence_no": 14, "title": "成品外观检验", "reason": "名称包含检验"},
                    ],
                },
            },
        }, {
            "id": 44,
            "role": "assistant",
            "content": "已处理。",
            "metadata_json": {"resolved_pending_message_id": 42},
        }]

        result = resolve(
            "再次选择电气性能检验",
            make_steps(),
            history,
            selected_step_id=11,
            pending_message_id=42,
        )

        self.assertEqual(result.status, "not_found")
        self.assertIsNone(result.selected_step_id)


if __name__ == "__main__":
    unittest.main()
