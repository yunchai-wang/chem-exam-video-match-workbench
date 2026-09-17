"""Recover only audited automatic label edits, preserving evidence and reviews.

Run with an explicitly selected backup taken before v0.1 tag filling. The
current state and the selected baseline are archived before the transaction.
No remote source or frozen historical question set is rewritten.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apps.l4_workbench.store import JsonStore
from apps.l4_workbench.label_library_sync import audit_tag_profiles
from apps.l4_workbench.mother_question import build_mother_question_run


def repair(store: JsonStore, baseline_path: Path) -> dict:
    baseline = json.loads(baseline_path.read_text())
    baseline_assets = {a['id']: a for a in baseline['question_assets']}
    state = store.load()
    if state.get('quality_repair', {}).get('version') == '2026-09-17.v1':
        return state['quality_repair']
    if state.get('selection_reviews') != baseline.get('selection_reviews') or state.get('mother_question_reviews') != baseline.get('mother_question_reviews'):
        raise ValueError('Review history changed after baseline; use a three-way manual reconciliation instead.')
    archive = store.path.parent / 'quality_repair' / datetime.now().strftime('%Y%m%dT%H%M%S')
    archive.mkdir(parents=True, exist_ok=False)
    shutil.copy2(store.path, archive / 'before.json')
    shutil.copy2(baseline_path, archive / 'baseline.json')
    report = {'version': '2026-09-17.v1', 'archive': str(archive), 'restored_assets': 0, 'reset_mappings': 0}
    with store.transaction() as current:
        if current['metadata']['state_revision'] != state['metadata']['state_revision']:
            raise ValueError('State changed while preparing recovery; retry with fresh evidence.')
        # All v0.1 automatic mappings are returned for review, including apparently
        # valid ones: the recorded one-to-one claim did not preserve ambiguity.
        for entry in current['unmatched_label_queue']:
            if entry.get('batch_align_version') == 'label-batch-align-v0.1' and entry.get('status') == '已映射':
                entry['retracted_mapping'] = {key: entry.get(key) for key in ('mapped_to', 'reason', 'batch_align_version')}
                entry.update(status='待映射', mapped_to=None, reason='旧自动映射已撤回，重新按语义与当前词表核验')
                entry.pop('batch_align_version', None)
                report['reset_mappings'] += 1
        for asset in current['question_assets']:
            before = baseline_assets.get(asset['id'])
            if not before:
                continue
            changed = False
            for key in ('tag_profile', 'normalized_fields'):
                if asset.get(key) != before.get(key):
                    if key in before:
                        asset[key] = deepcopy(before[key])
                    else:
                        asset.pop(key, None)
                    changed = True
            report['restored_assets'] += int(changed)
        baseline_candidates = {c['asset_id']: c for run in baseline['selection_runs'] for c in run['results']}
        # Historical runs stay present. Only the active pool is repaired; earlier
        # fill/proposal runs are explicitly superseded rather than deleted.
        selection = current['selection_runs'][-1]
        for candidate in selection['results']:
            original = baseline_candidates.get(candidate['asset_id'])
            if original:
                for key in ('tag_profile', 'units'):
                    candidate[key] = deepcopy(original[key])
        for fill in current.get('ai_tag_fill_runs', []):
            if fill.get('rule_version') == 'ai-unit-tag-fill-v0.1':
                fill['status'] = 'retracted_by_quality_audit'
        for run in current['mother_question_runs']:
            run['status'] = 'superseded_by_quality_audit'
        library = next(x for x in reversed(current['label_library_snapshots']) if x.get('vocabulary'))
        audit, queue = audit_tag_profiles(current['question_assets'], library, current['unmatched_label_queue'])
        library['audit'] = audit
        current['unmatched_label_queue'] = queue
        diagnosis = next((d for d in current['diagnostic_runs'] if d['id'] == selection.get('diagnostic_run_id')), None)
        mother = build_mother_question_run(selection, current['selection_reviews'], current['question_assets'], diagnosis)
        current['mother_question_runs'].append(mother)
        report.update(matched_rate=audit['matched_rate'], mother_run_id=mother['id'])
        current['quality_repair'] = report
        current['events'].append({'id': 'quality-repair-20260917', 'kind': 'quality.repaired',
                                  'message': '已撤回旧自动映射与待校准补标；保留原始资料、视频索引和审核历史。',
                                  'created_at': datetime.now().isoformat(timespec='seconds')})
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(repair(JsonStore(args.state), args.baseline), ensure_ascii=False, indent=2))
