#!/usr/bin/env python3
"""Root post-run personal review of automatic image admissions; no guest launch."""
import argparse
import os
from pathlib import Path
import time

from exploration_common import *
import image_operator
import matched_trial


def runtime_evidence(cfg, root):
    if not image_operator.enabled(cfg):
        raise RuntimeError('Post-run image review requires explicit automatic packet')
    result = read(root / 'RESULT.json')
    if (result.get('status') != 'AWAITING_ROOT_REVIEW'
            or result.get('automatic_image_admission') is not True
            or result.get('home_operator_attestation') is not False
            or result.get('settings_operator_attestation') is not False
            or not strict_final(result.get('exit_code'), result.get('final_receipt', ''))
            or result.get('error') is not None or result.get('dirty') is not False
            or any(result.get(key) is not True for key in ['confirmed_clean_receipt', 'strict_retirement',
                'lease_available', 'seed_unchanged', 'immutable_base_unchanged',
                'home_image_admitted', 'settings_image_admitted', 'exact_candidate_mapping_observed'])
            or any(item['signal'] in (9, 15) for item in result['signals'])):
        raise RuntimeError('Only complete clean unrepaired runtime evidence may be reviewed')
    for relative, expected in read(root.parent / 'EXPLORATION-SOURCE-SHA256.json').items():
        if digest(root.parent / relative) != expected:
            raise RuntimeError('Reviewed packet source changed')
    for expected in [result['frontend'], result['worker'], read(root / 'consumed.json')['controller']]:
        if not expected or same_process(expected):
            raise RuntimeError('Post-run review requires independent original process retirement')
    group = Path(read(root / 'command.json')['cgroup'])
    if not group.is_relative_to('/sys/fs/cgroup') or group.exists():
        raise RuntimeError('Post-run review requires original cgroup absent')
    with free_lease(cfg['state']):
        pass
    for scene in ['home', 'settings']:
        admission = image_operator.validate_admission(root, scene)
        if admission['reference'] != cfg['imageBoundOperator']['references'][scene]:
            raise RuntimeError('Automatic admission used a different prepared reference')
    hello = result['first_hello_monotonic_ns']
    schedule = result['matched_diagnostic']
    tracker = matched_trial.Tracker(cfg)
    tracker.observe(root, hello, hello + 135 * SECOND, rows(root / 'session.log'))
    if tracker.finish(root, hello, schedule['stop_ns']) != schedule:
        raise RuntimeError('Actual input schedule/readiness changed before post-run review')
    receipt = read(root / 'cache-trial.json')
    if digest(root / 'cache-trial.json') != result['cache_evidence_sha256'] or receipt['status'] != 'AWAITING_ROOT_REVIEW':
        raise RuntimeError('Provisional native cache evidence changed')
    return result, receipt


def qualify(cfg, root, inspected_home, inspected_settings, wifi_name):
    if not inspected_home or not inspected_settings or wifi_name != 'qemu-ios':
        raise RuntimeError('Root must personally inspect exact admitted Home and full Settings/qemu-ios PNGs')
    result, receipt = runtime_evidence(cfg, root)
    # Refuse overwrite even after an interrupted/failed review. Preserve evidence.
    targets = ['ROOT-SCENE-REVIEW.json', 'cache-trial-qualified.json', 'QUALIFIED-RESULT.json']
    if any((root / name).exists() for name in targets):
        raise RuntimeError('Preserve existing or interrupted post-run review; no overwrite')
    import cache_trial
    if cfg['cacheTrial']['mode'] != 'off':
        current = cache_trial.snapshot(cfg['cacheTrial']['directory'], cfg['pins']['engine']['sha256'])
        if current != receipt['after']:
            raise RuntimeError('Native cache changed after retired runtime')
    scene_pins = {scene: {'admission_sha256': digest(root / (scene + '-admitted.json')),
                  'capture': image_operator.validate_admission(root, scene)['capture']}
                  for scene in ['home', 'settings']}
    review = {'status': 'ROOT_PERSONAL_POSTRUN_SCENE_REVIEW_PASS',
              'scope': 'PERSONAL_POSTRUN_REVIEW_NOT_INPUT_TIME_INSPECTION',
              'model_inspected_home': True, 'model_inspected_settings': True,
              'observed_wifi_name': wifi_name, 'scenes': scene_pins,
              'reviewed_monotonic_ns': time.monotonic_ns(),
              'raw_result_sha256': digest(root / 'RESULT.json'),
              'raw_cache_receipt_sha256': digest(root / 'cache-trial.json'),
              'packet_sha256': digest(root.parent / 'EXPLORATION-SOURCE-SHA256.json'),
              'reviewer': process(os.getpid()), 'performance_qualified': False}
    save(root / targets[0], review)
    qualified_cache = dict(receipt, status='SCOPED_PASS',
                           root_scene_review_sha256=digest(root / targets[0]))
    save(root / targets[1], qualified_cache)
    qualified = dict(result, status='SCOPED_PASS',
                     root_scene_review_sha256=digest(root / targets[0]),
                     cache_evidence_sha256=digest(root / targets[1]))
    qualified['matched_diagnostic'] = dict(result['matched_diagnostic'],
        status='DIAGNOSTIC_SCHEDULE_COMPLETED', postrun_review_required=True)
    save(root / targets[2], qualified)
    return qualified


def validate_qualification(cfg, producer, result_path, cache_path):
    root = Path(result_path).parent
    if Path(result_path).name != 'QUALIFIED-RESULT.json' or Path(cache_path) != root / 'cache-trial-qualified.json':
        raise RuntimeError('Image-bound REUSE requires separate Root-qualified producer files')
    original = read(root.parent / 'EXPLORATION-INPUTS.json')
    runtime, receipt = runtime_evidence(original, root)
    import cache_trial
    if cache_trial.identity(original) != cache_trial.identity(cfg):
        raise RuntimeError('Root-qualified producer packet identity changed')
    review = read(root / 'ROOT-SCENE-REVIEW.json')
    if (review.get('status') != 'ROOT_PERSONAL_POSTRUN_SCENE_REVIEW_PASS'
            or review.get('model_inspected_home') is not True or review.get('model_inspected_settings') is not True
            or review.get('observed_wifi_name') != 'qemu-ios'
            or review.get('raw_result_sha256') != digest(root / 'RESULT.json')
            or review.get('raw_cache_receipt_sha256') != digest(root / 'cache-trial.json')
            or review.get('packet_sha256') != digest(root.parent / 'EXPLORATION-SOURCE-SHA256.json')
            or review.get('reviewed_monotonic_ns', 0) <= runtime['matched_diagnostic']['stop_ns']
            or producer.get('root_scene_review_sha256') != digest(root / 'ROOT-SCENE-REVIEW.json')):
        raise RuntimeError('Actual independent Root post-run review missing or changed')
    for scene in ['home', 'settings']:
        admission = image_operator.validate_admission(root, scene)
        if review['scenes'].get(scene) != {'admission_sha256': digest(root / (scene + '-admitted.json')),
                                          'capture': admission['capture']}:
            raise RuntimeError('Root-reviewed actual scene binding changed')
    expected_cache = dict(receipt, status='SCOPED_PASS', root_scene_review_sha256=digest(root / 'ROOT-SCENE-REVIEW.json'))
    expected_result = dict(runtime, status='SCOPED_PASS', root_scene_review_sha256=digest(root / 'ROOT-SCENE-REVIEW.json'),
                           cache_evidence_sha256=digest(root / 'cache-trial-qualified.json'))
    expected_result['matched_diagnostic'] = dict(runtime['matched_diagnostic'], status='DIAGNOSTIC_SCHEDULE_COMPLETED',
                                               postrun_review_required=True)
    if read(cache_path) != expected_cache or producer != expected_result:
        raise RuntimeError('Root-qualified derivative changed from actual raw evidence')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-inspected-home', action='store_true')
    parser.add_argument('--model-inspected-settings', action='store_true')
    parser.add_argument('--wifi-name', required=True)
    args = parser.parse_args()
    qualify(read(P / 'EXPLORATION-INPUTS.json'), R,
            args.model_inspected_home, args.model_inspected_settings, args.wifi_name)
    print('Separate qualified files written. Original runtime images/admissions/results retained unchanged.')


if __name__ == '__main__':
    main()
