"""Aggregate persisted evaluations by execution time, without exposing credentials."""
from datetime import datetime, timedelta, timezone
from math import ceil
from zoneinfo import ZoneInfo

from .db import connection

WINDOWS = {'12h': (12, 30), '1d': (24, 60), '7d': (168, 360)}
COUNTERS = ('evaluations', 'identity_matches', 'suspected_swaps', 'uncertain', 'unavailable',
            'good', 'warning', 'bad', 'unknown', 'candy_correct', 'candy_scored',
            'candy_invalid', 'candy_unscored', 'candy_unknown_rounds')


def metrics(rows):
    counts = {name: sum(row[name] for row in rows) for name in COUNTERS}
    counts['candy_accuracy'] = counts['candy_correct'] / counts['candy_scored'] if counts['candy_scored'] else None
    counts['identity_match_rate'] = counts['identity_matches'] / counts['evaluations'] if counts['evaluations'] else None
    # Both signals are required; incomplete scored coverage lowers the score.
    fingerprint_rate = counts['identity_match_rate']
    candy_rate = counts['candy_accuracy']
    # Diagnostic counters can overlap, so coverage uses its own SQL count.
    counts['evidence_coverage'] = sum(row.get('scored_evaluations', 0) for row in rows) / counts['evaluations'] if counts['evaluations'] else 0
    base_score = fingerprint_rate * .55 + candy_rate * .45 if fingerprint_rate is not None and candy_rate is not None else None
    if base_score is None:
        counts['stability_score'] = None
    else:
        penalty = ((counts['bad'] / counts['evaluations']) * 0.20 +
                   ((counts['warning'] + counts['unknown']) / counts['evaluations']) * 0.10) if counts['evaluations'] else 0
        counts['stability_score'] = max(0, min(1, base_score * counts['evidence_coverage'] - penalty))
    counts['status'] = ('empty' if not counts['evaluations'] else 'bad' if counts['bad'] else
                        'warning' if counts['warning'] or (counts['unknown'] and counts['good']) else
                        'good' if counts['good'] else 'unknown')
    return counts


def time_range(window, now, date_from=None, date_to=None):
    zone = ZoneInfo('Asia/Shanghai')
    if window == 'custom':
        today = now.astimezone(zone).date()
        first = date_from or date_to or today
        last = date_to or today
        if first > last:
            raise ValueError('起始日期不能晚于结束日期')
        start = datetime.combine(first, datetime.min.time(), zone)
        end = datetime.combine(last + timedelta(days=1), datetime.min.time(), zone)
        minutes = max(1, ceil((end - start).total_seconds() / 60 / 28))
    else:
        hours, minutes = WINDOWS[window]
        end = now
        start = end - timedelta(hours=hours)
    return start, end, minutes


def selection_filters(start, end, now, domain=None, model=None, key_hash=None,
                      key_recorded=None, verdict=None, fingerprint=None, top_model=None, candy=None,
                      include_unavailable=True):
    zone = ZoneInfo('Asia/Shanghai')
    upper = min(end, now)
    filters = ["((time_precision = 'timestamp' AND tested_at >= %s AND tested_at < %s) OR (time_precision = 'date' AND tested_date >= %s AND tested_date <= %s))"]
    args = [start, upper, start.astimezone(zone).date(), (upper - timedelta(microseconds=1)).astimezone(zone).date()]
    for field, value in (('domain', domain.lower() if domain else None), ('model', model),
                         ('key_hash', key_hash), ('verdict', verdict), ('fingerprint', fingerprint),
                         ('top_model', top_model), ('candy', candy)):
        if value is not None:
            filters.append(f'{field} = %s'); args.append(value)
    if key_recorded is not None:
        filters.append('key_hash IS NOT NULL' if key_recorded else 'key_hash IS NULL')
    if not include_unavailable:
        filters.append("verdict IS DISTINCT FROM '无法评测'")
    return filters, args


def classify_event(event):
    parts = (event['candy'] or '').split('/')
    valid = len(parts) == 2 and all(p.isascii() and p.isdigit() and len(p) <= 9 for p in parts)
    correct, scored = map(int, parts) if valid else (0, 0)
    valid = valid and correct <= scored
    rate = correct / scored if valid and scored and event['verdict'] != '无法评测' else None
    verdict = event['verdict']
    if verdict == '真' and valid and scored and correct * 2 <= scored:
        verdict = '存疑'
    status = ('unknown' if verdict == '无法评测' else
              'bad' if verdict == '换模' or (rate is not None and .5 < rate < .6) else
              'good' if verdict == '真' and rate is not None and rate >= .8 and '?' not in (event['candy_answers'] or '') else 'warning')
    return {**event, 'verdict': verdict, 'status': status, 'candy_accuracy': rate}


def is_reference(group):
    return bool(group['key_hash'] and group['evaluations'] >= 3
                and group['stability_score'] is not None and group['stability_score'] >= .8
                and not group['bad'] and group['latest_status'] == 'good')


def analyze(window='12h', group_by='domain', domain=None, model=None, limit=20, offset=0, now=None,
            date_from=None, date_to=None, top_per_model=None, include_unavailable=True, **dimensions):
    now = now or datetime.now(timezone.utc)
    start, end, minutes = time_range(window, now, date_from, date_to)
    seconds = minutes * 60
    bucket_count = ceil((end - start).total_seconds() / seconds)
    # Grouping expressions come only from this fixed map, never request text.
    key_expr = 'key_hash' if group_by != 'domain' else 'NULL::text'
    prefix_expr = 'key_prefix' if group_by != 'domain' else 'NULL::text'
    model_expr = 'model' if group_by == 'domain_key_model' else 'NULL::text'
    sequence_order = "CASE WHEN time_precision = 'date' THEN tested_date::text || 'Z' ELSE to_char(tested_at AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM-DD\"T\"HH24:MI:SS.US') END"
    filters, args = selection_filters(start, end, now, domain, model, include_unavailable=include_unavailable, **dimensions)
    sql = f"""
    WITH source AS (
      SELECT id, domain, {key_expr} AS key_hash, {prefix_expr} AS key_prefix,
             {model_expr} AS model, CASE WHEN time_precision = 'date' THEN greatest(tested_at, %s::timestamptz) ELSE tested_at END AS tested_at,
             tested_at AS original_tested_at, {sequence_order} AS sequence_order, verdict, candy, candy_answers,
             CASE WHEN candy ~ '^[0-9]{{1,9}}/[0-9]{{1,9}}$' THEN split_part(candy, '/', 1)::bigint END AS correct,
             CASE WHEN candy ~ '^[0-9]{{1,9}}/[0-9]{{1,9}}$' THEN split_part(candy, '/', 2)::bigint END AS scored
      FROM ai_gateway_eval_results WHERE {' AND '.join(filters)}
    ), checked AS (
      SELECT *, COALESCE(correct <= scored, false) AS valid,
        CASE WHEN verdict = '真' AND COALESCE(correct <= scored, false)
                  AND scored > 0 AND correct * 2 <= scored THEN '存疑' ELSE verdict END AS effective_verdict,
        COALESCE(length(candy_answers) - length(replace(candy_answers, '?', '')), 0) AS unknown_rounds
      FROM source
    ), classified AS (
      SELECT *, CASE
        WHEN effective_verdict = '无法评测' THEN 'unknown'
        WHEN effective_verdict = '换模' OR (valid AND scored > 0 AND correct::numeric / scored > 0.5 AND correct::numeric / scored < 0.6) THEN 'bad'
        WHEN effective_verdict = '真' AND valid AND scored > 0 AND correct::numeric / scored >= 0.8 AND unknown_rounds = 0 THEN 'good'
        ELSE 'warning' END AS quality
      FROM checked
    )
    SELECT domain, key_hash, max(key_prefix) AS key_prefix, model,
      floor(extract(epoch FROM (tested_at - %s::timestamptz)) / %s)::int AS bucket,
      max(original_tested_at) AS last_tested_at,
      max(sequence_order) AS last_sequence_order,
      (array_agg(quality ORDER BY sequence_order DESC, id DESC))[1] AS latest_status,
      count(*) AS evaluations,
      count(*) FILTER (WHERE valid AND scored > 0 AND verdict IS DISTINCT FROM '无法评测') AS scored_evaluations,
      count(*) FILTER (WHERE effective_verdict = '真') AS identity_matches,
      count(*) FILTER (WHERE effective_verdict = '换模') AS suspected_swaps,
      count(*) FILTER (WHERE effective_verdict IS NULL OR effective_verdict NOT IN ('真', '换模', '无法评测')) AS uncertain,
      count(*) FILTER (WHERE effective_verdict = '无法评测') AS unavailable,
      count(*) FILTER (WHERE quality = 'good') AS good,
      count(*) FILTER (WHERE quality = 'warning') AS warning,
      count(*) FILTER (WHERE quality = 'bad') AS bad,
      count(*) FILTER (WHERE quality = 'unknown') AS unknown,
      COALESCE(sum(correct) FILTER (WHERE valid AND effective_verdict IS DISTINCT FROM '无法评测'), 0)::bigint AS candy_correct,
      COALESCE(sum(scored) FILTER (WHERE valid AND effective_verdict IS DISTINCT FROM '无法评测'), 0)::bigint AS candy_scored,
      count(*) FILTER (WHERE NOT valid) AS candy_invalid,
      count(*) FILTER (WHERE valid AND scored = 0) AS candy_unscored,
      sum(unknown_rounds)::bigint AS candy_unknown_rounds
    FROM classified GROUP BY domain, key_hash, model, bucket
    ORDER BY domain, key_hash NULLS LAST, model, bucket
    """
    with connection() as conn:
        rows = conn.execute(sql, [start, *args, start, seconds]).fetchall()
    groups, overall_buckets = {}, [[] for _ in range(bucket_count)]
    for row in rows:
        identity = (row['domain'], row['key_hash'], row['model'])
        group = groups.setdefault(identity, {'domain': row['domain'], 'key_hash': row['key_hash'],
                                              'key_prefix': row['key_prefix'], 'model': row['model'], 'rows': []})
        group['rows'].append(row)
        overall_buckets[row['bucket']].append(row)
    bucket_times = [(start + timedelta(minutes=minutes * i)).isoformat() for i in range(bucket_count)]

    def timeline(bucket_rows):
        return [{'start': stamp, 'end': (start + timedelta(minutes=minutes * (i + 1))).isoformat(),
                 **metrics(bucket_rows[i])} for i, stamp in enumerate(bucket_times)]

    ranked = []
    for group in groups.values():
        group_rows = group.pop('rows')
        buckets = [[] for _ in range(bucket_count)]
        for row in group_rows:
            buckets[row['bucket']].append(row)
        group.update(metrics(group_rows))
        group['last_tested_at'] = max(row['last_tested_at'] for row in group_rows).isoformat()
        group['latest_status'] = max(group_rows, key=lambda row: row['last_sequence_order'])['latest_status']
        group['timeline'] = timeline(buckets)
        group['sequence'] = []
        ranked.append(group)
    ranked.sort(key=lambda g: (g['stability_score'] is None, -(g['stability_score'] or 0),
                               -g['evaluations'], g['domain'], g['key_hash'] or '', g['model'] or ''))
    for index, group in enumerate(ranked, 1):
        group['rank'] = index
        group['sequence_total'] = group['evaluations']
    recommended = next((g for g in ranked if is_reference(g)), None)
    recommendation = {field: recommended[field] for field in ('rank', 'domain', 'key_hash', 'key_prefix', 'model', 'stability_score', 'evaluations')} if recommended else None
    page = ranked[offset:offset + limit]
    leaderboards = []
    if top_per_model is not None:
        by_model = {}
        for group in ranked:
            by_model.setdefault(group['model'], []).append(group)
        page = []
        for model_name, channels in sorted(by_model.items()):
            top = channels[:top_per_model]
            for rank, group in enumerate(top, 1):
                group['model_rank'] = rank
                group['is_reference'] = is_reference(group)
            leaderboards.append({'model': model_name, 'total_channels': len(channels),
                                 'evaluations': sum(g['evaluations'] for g in channels), 'channels': top})
            page.extend(top)
    events = []
    if page:
        selected, selected_args = [], []
        for group in page:
            selected.append(f"(domain = %s AND {key_expr} IS NOT DISTINCT FROM %s AND {model_expr} IS NOT DISTINCT FROM %s)")
            selected_args.extend([group['domain'], group['key_hash'], group['model']])
        with connection() as conn:
            events = conn.execute(f"""WITH recent AS (
                SELECT id, domain, key_hash, key_prefix, model, tested_at, tested_date,
                       time_precision, model_rate, recharge_rate, request_ip,
                       fingerprint, top_model, verdict, candy, candy_answers, note,
                       row_number() OVER (PARTITION BY domain, {key_expr}, {model_expr} ORDER BY {sequence_order} DESC, id DESC) AS rn
                FROM ai_gateway_eval_results WHERE {' AND '.join(filters)} AND ({' OR '.join(selected)})
                ) SELECT * FROM recent WHERE rn <= 300 ORDER BY tested_at, id""", [*args, *selected_args]).fetchall()
    for event in events:
        event.pop('rn')
        identity = (event['domain'], event['key_hash'] if group_by != 'domain' else None,
                    event['model'] if group_by == 'domain_key_model' else None)
        group = groups[identity]
        group['sequence'].append(classify_event({**event, 'tested_at': event['tested_at'].isoformat(),
                                                 'tested_date': str(event['tested_date'])}))
    return {'window': window, 'group_by': group_by, 'from': start.isoformat(), 'to': end.isoformat(),
            'bucket_minutes': minutes, 'timezone': 'Asia/Shanghai', 'summary': metrics(rows),
            'timeline': timeline(overall_buckets), 'total_groups': len(ranked),
            'groups': page,
            'recommendation': recommendation,
            'leaderboards': leaderboards,
            'models': sorted({row['model'] for row in rows if row['model']}),
            'limit': limit, 'offset': offset}


def dashboard(window='12h', date_from=None, date_to=None, history_limit=30, history_offset=0,
              now=None, include_unavailable=False, **dimensions):
    now = now or datetime.now(timezone.utc)
    data = analyze(window=window, group_by='domain_key_model', now=now, date_from=date_from,
                   date_to=date_to, top_per_model=10, include_unavailable=include_unavailable, **dimensions)
    start, end, _ = time_range(window, now, date_from, date_to)
    filters, args = selection_filters(start, end, now, include_unavailable=include_unavailable, **dimensions)
    where = ' WHERE ' + ' AND '.join(filters)
    # Date-only records sort after that day's precise records in ascending
    # sequences, and therefore before them in the reverse chronological list.
    order = "CASE WHEN time_precision = 'date' THEN tested_date::text || 'Z' ELSE to_char(tested_at AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM-DD\"T\"HH24:MI:SS.US') END DESC, id DESC"
    with connection() as conn:
        total = conn.execute('SELECT count(*) AS n FROM ai_gateway_eval_results' + where, args).fetchone()['n']
        records = conn.execute('SELECT * FROM ai_gateway_eval_results' + where + ' ORDER BY ' + order + ' LIMIT %s OFFSET %s',
                               [*args, history_limit, history_offset]).fetchall()
        models = conn.execute('SELECT DISTINCT model FROM ai_gateway_eval_results ORDER BY model').fetchall()
        domains = conn.execute('SELECT DISTINCT domain FROM ai_gateway_eval_results ORDER BY domain').fetchall()
    return {field: data[field] for field in ('window', 'from', 'to', 'timezone', 'summary', 'total_groups', 'leaderboards', 'models')} | {
        'models': [item['model'] for item in models], 'domains': [item['domain'] for item in domains],
        'history': {'items': [classify_event(row) for row in records], 'total': total,
                    'limit': history_limit, 'offset': history_offset}}
