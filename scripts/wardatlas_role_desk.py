"""Role-first, read-only projection of live ownership witnesses.

No source-oracle imports, writes, SOP claims or workload completeness inference.
The discovery and selected reverse queries both require full ACL-bound paths.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs
from verify_falkordb_live import query_result, result_rows
from wardatlas_shift_board import (
    CONVEYOR_PATH_QUERY, CONVEYOR_EVENT_QUERY, dock_role_query,
    normalize_timestamp, CONVEYOR_OUTAGE_AT,
)

HTML_PATH = Path(__file__).resolve().parent / 'ui/role-desk.html'
ERROR = 'Invalid role responsibility request.'


def parse_request(query: str) -> dict:
    try:
        values = parse_qs(query, keep_blank_values=True, strict_parsing=True, max_num_fields=6)
        if any(len(v) != 1 for v in values.values()):
            raise ValueError
        v = {k: x[0] for k, x in values.items()}
        context = v.get('context', 'conveyor')
        allowed = {'scope', 'context', 'role'} | ({'as_of'} if context == 'conveyor' else {'effective_at', 'known_at'})
        if context not in {'conveyor', 'dock'} or set(v) - allowed or v.get('scope', 'operations') != 'operations':
            raise ValueError
        if 'role' in v and not re.fullmatch(r'ROLE-[A-Z0-9-]{1,80}', v['role']):
            raise ValueError
        if context == 'conveyor':
            clocks = {'as_of': normalize_timestamp(v.get('as_of', CONVEYOR_OUTAGE_AT))}
        else:
            clocks = {k: normalize_timestamp(v[k]) for k in ('effective_at', 'known_at')}
        return {'context': context, 'role': v.get('role'), 'clocks': clocks}
    except (ValueError, KeyError, OverflowError):
        raise ValueError(ERROR) from None


def unavailable() -> dict:
    # Deliberately identical for unknown IDs, denied witnesses, empty graphs,
    # missing edges and backend failures. Never echo an unestablished selector.
    return {'scope': 'operations', 'state': 'unknown',
            'message': 'Role responsibility evidence is unavailable.', 'roles': [],
            'selected_role': None, 'responsibilities': [], 'ownership_witnesses': [],
            'event': None, 'clocks': None, 'safe_asserted': False,
            'resolution_asserted': False, 'workload_complete': False}


def guard(query: str, predicate: str) -> str:
    before, sep, after = query.partition('\nRETURN ')
    if not sep:
        raise ValueError('Expected read query')
    return before + '\n  AND ' + predicate + sep + after


def queries(selection: dict, selected: bool = False) -> dict:
    # Every interpolated clock has been normalized by the accepted timestamp
    # validator. Role selection is a Cypher parameter, never string interpolation.
    clocks = selection['clocks']
    if selection['context'] == 'conveyor':
        at = clocks['as_of']
        ownership = guard(CONVEYOR_PATH_QUERY,
            f"isrc.observed_at <= '{at}' AND esrc.observed_at <= '{at}' "
            f"AND ssrc.observed_at <= '{at}' AND owns.observed_at <= '{at}'")
        event = guard(CONVEYOR_EVENT_QUERY, f"event.event_at <= '{at}' AND src.observed_at <= '{at}'")
        event = event.replace('ORDER BY event.event_at', 'ORDER BY event.event_at DESC') + '\nLIMIT 1'
        result = {'event': event}
    else:
        ownership = dock_role_query(clocks['effective_at'], clocks['known_at'])
        result = {}
    role = selection.get('role') if selected else None
    if role is not None:
        ownership = "CYPHER role='" + role + "'\n" + guard(ownership, 'r.id = $role')
    result['ownership'] = ownership + '\nLIMIT 128'
    return result


def witnesses(rows: list[dict], context: str) -> list[dict]:
    result = []
    for row in rows:
        cards = []
        if context == 'conveyor':
            for prefix in ('incident', 'equipment', 'service', 'ownership'):
                cards.append({'source_id': row[prefix + '_source_id'],
                              'title': row[prefix + '_source_title'],
                              'observed_at': row[prefix + '_observed_at']})
        else:
            cards.append({'source_id': row['source_id'], 'title': row['source_title'],
                          'observed_at': row['source_observed_at']})
        result.append({'path': [row[k + '_id'] for k in ('incident', 'equipment', 'service', 'department', 'role')],
                       'sources': cards})
    return sorted(result, key=lambda p: (p['path'], str(p['sources'])))


def read_responsibilities(service, selection: dict) -> dict:
    """Hold accepted app graph lock over discovery and role-constrained reads."""
    try:
        with service.graph_lock:
            connection = service._connection()
            try:
                def rows(query):
                    return result_rows(query_result(connection.command('GRAPH.RO_QUERY', service.graph_name, query)))
                q = queries(selection)
                discovery = rows(q['ownership'])
                event_rows = rows(q['event']) if 'event' in q else []
                visible = sorted({r['role_id'] for r in discovery})
                role = selection['role']
                if not visible or (role is not None and role not in visible):
                    return unavailable()
                selected_rows = rows(queries(selection, True)['ownership']) if role else discovery
            finally:
                connection.close()
        if not selected_rows:
            return unavailable()
        event = None
        restored = False
        if selection['context'] == 'conveyor':
            if not event_rows or event_rows[0]['value'] not in {'available', 'unavailable'}:
                return unavailable()
            e = event_rows[0]
            restored = e['value'] == 'available'
            event = {'id': e['event_id'], 'value': e['value'], 'event_at': e['event_at'],
                     'source': {'source_id': e['source_id'], 'title': e['source_title'], 'observed_at': e['source_observed_at']}}
        paths = witnesses(selected_rows, selection['context'])
        return {'scope': 'operations', 'context': selection['context'],
                'state': 'restored-observed' if restored else 'witnessed',
                'message': ('Observed restoration removes active responsibilities here; safety and resolution remain unasserted.'
                            if restored else 'Only source-witnessed paths in this independent context; not a complete workload.'),
                'roles': sorted({p['path'][-1] for p in paths}), 'selected_role': role,
                'responsibilities': [] if restored else paths, 'ownership_witnesses': paths,
                'event': event, 'clocks': dict(selection['clocks']), 'safe_asserted': False,
                'resolution_asserted': False, 'workload_complete': False}
    except Exception:
        return unavailable()


def mount_role_desk(handler, service, parsed) -> bool:
    if parsed.path not in {'/roles', '/api/role-responsibilities'}:
        return False
    try:
        selection = parse_request(parsed.query)
    except ValueError:
        handler._error(400, ERROR)
        return True
    if parsed.path == '/roles':
        handler._send(200, HTML_PATH.read_text(encoding='utf-8'))
    else:
        handler._send(200, service.role_responsibilities(selection))
    return True
