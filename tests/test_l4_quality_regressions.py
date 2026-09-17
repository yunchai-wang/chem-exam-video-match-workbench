"""Business counterexamples from the September production audit."""
import unittest
from copy import deepcopy

from apps.l4_workbench.label_library_sync import classify_unmatched_label, audit_tag_profiles
from apps.l4_workbench.ai_unit_tag_fill import LabelResolver, _propose_candidate
from apps.l4_workbench.video_evidence_index import _prefer_transcript, _kind_from_name, _kind_from_collection_attachment
from apps.l4_workbench.stage_executors import _confirmed_artifact
from apps.l4_workbench.video_evidence import _coverage_candidate
from tests import test_l4_service as fixtures
from tests.test_l4_ai_unit_tag_fill import multi_unit_candidate, multi_unit_asset, SNAPSHOT, QUEUE


class QualityRegressions(unittest.TestCase):
    def test_script_and_unfinalized_transcript_do_not_claim_final_status(self):
        self.assertEqual(_kind_from_name('脚本.docx'), '其他')
        self.assertEqual(_kind_from_collection_attachment('逐字稿初稿.docx', ''), '其他')
        self.assertEqual(_kind_from_name('预定稿.docx'), '其他')

    def test_unopened_transcript_pointer_is_not_coverage_evidence(self):
        video = {'video_id': 'v', 'video_name': '课', 'transcript_status': '索引定稿-待打开核验',
                 'screenshot_tokens': [], 'evidence_level': 'E1', 'transcript_evidence': '定稿指针'}
        result = _coverage_candidate(video, '结构', ['任务'], ['知识'], 'passed')
        self.assertFalse(result['coverage_candidate'])
    def test_unique_containment_is_not_semantic_equivalence(self):
        result = classify_unmatched_label({'dimension': 'knowledge', 'label': '实验基本操作'}, {'实验基本操作-加热'})
        self.assertEqual(result['action'], 'needs_review')

    def test_compound_label_cannot_collapse_to_one_component(self):
        result = classify_unmatched_label({'dimension': 'knowledge', 'label': '化学变化/物理变化'}, {'化学变化'})
        self.assertEqual(result['action'], 'project_extension')

    def test_one_to_many_mapping_keeps_all_targets(self):
        snapshot = deepcopy(SNAPSHOT)
        snapshot['old_to_new'] = [
            {'dimension': 'knowledge', 'old': '旧概念', 'new': label, 'status': '现行'}
            for label in ['空气与氧气', '金属与材料']
        ]
        _, queue = audit_tag_profiles([{'id': 'a', 'tag_profile': {'knowledge': {'all': ['旧概念']}}}], snapshot, [])
        self.assertEqual(set(queue[0]['suggested_labels']), {'空气与氧气', '金属与材料'})
        self.assertEqual(classify_unmatched_label(queue[0], {'空气与氧气', '金属与材料'})['action'], 'needs_review')

    def test_pending_suggestion_is_not_an_approved_mapping(self):
        self.assertIsNone(LabelResolver(SNAPSHOT, QUEUE).resolve('question', '写方程式')[0])

    def test_upstream_from_another_question_set_cannot_be_used(self):
        artifact = {'target_stage': 'lesson_plan', 'version': 1, 'confirmation': {'version': 1, 'primary_output_id': 'x'},
                    'production_input_fingerprint': 'old', 'outputs': [{'id': 'x', 'path': __file__, 'kind': 'docx'}]}
        self.assertIsNone(_confirmed_artifact({'artifacts': [artifact]}, 'lesson_plan', {'production_input_fingerprint': 'new'}))

    def test_calculation_and_judgement_are_not_writing_or_comparison(self):
        asset = multi_unit_asset()
        asset['raw_text'] = '(1)求溶质质量分数。(2)判断化学方程式是否正确。'
        proposal = _propose_candidate(multi_unit_candidate(), asset, [], LabelResolver(SNAPSHOT, QUEUE))
        self.assertNotIn('比较溶质质量分数', proposal['units'][0]['question'])
        self.assertNotIn('写化学反应方程式', proposal['units'][1]['question'])

    def test_latest_dated_final_is_independent_of_source_order(self):
        newer = {'kind': '定稿', 'title': '定稿20260916.docx', 'url': 'new'}
        older = {'kind': '定稿', 'title': '定稿20250101.docx', 'url': 'old'}
        self.assertEqual(_prefer_transcript([newer, older])['url'], 'new')

    def test_undated_competing_finals_are_explicitly_unresolved(self):
        result = _prefer_transcript([
            {'kind': '定稿', 'title': '甲定稿.docx', 'url': 'a'},
            {'kind': '定稿', 'title': '乙定稿.docx', 'url': 'b'},
        ])
        self.assertEqual(result.get('selection_status'), '版本待核验')


class ProductionRegressions(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ServiceTests()
        self.fixture.setUp()
        self.service = self.fixture.service
        self.sid = self.fixture._seed_selection_run()
        self.mother = self.service.create_mother_question_run({'selection_run_id': self.sid})
        self.service.update_project({'intervention_strategies': {s: 'auto' for s in fixtures.STAGES}})

    def tearDown(self):
        self.fixture.tearDown()

    def test_auto_strategy_can_prepare_nonexception_groups_without_teacher(self):
        run = self.service.start_run({'deliverables': ['lesson_plan']})
        self.assertEqual(run['status'], 'completed')
        self.assertEqual(self.service.get_state()['mother_question_reviews'], [])

    def test_tag_change_invalidates_mother_proposal(self):
        with self.service.store.transaction() as state:
            state['selection_runs'][-1]['results'][0]['tag_profile']['question'] = ['另一种作答任务']
        updated = self.service.create_mother_question_run({'selection_run_id': self.sid})
        self.assertNotEqual(updated['id'], self.mother['id'])
        self.assertEqual(updated['groups'][0]['mode'], '递进题组')

    def test_chosen_confirmation_resumes_with_updated_groups(self):
        self.service.update_project({'intervention_strategies': {'mother_question': 'confirm'}})
        run = self.service.start_run({'deliverables': ['lesson_plan']})
        self.assertEqual(run['status'], 'waiting')
        self.service.batch_confirm_mother_question_groups({'mother_question_run_id': self.mother['id']})
        review = self.service.get_state()['reviews'][-1]
        self.assertEqual(self.service.approve_review(review['id'])['status'], 'completed')


if __name__ == '__main__':
    unittest.main()
