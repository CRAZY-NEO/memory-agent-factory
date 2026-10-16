"""Offline baselines only. Real generation uses contracts, never these implementations."""

FIELD = """def check_fields(payload):
    rows = []
    for record in payload['records']:
        missing = []
        for key in payload['required_fields']:
            value = record['fields'].get(key)
            if value is None or isinstance(value, str) and not value.strip() or isinstance(value, (list, dict)) and not value:
                missing.append(key)
        rows.append({'record_id': record['record_id'], 'missing_fields': missing, 'complete': not missing})
    return {'records': rows, 'complete': all(row['complete'] for row in rows)}
"""

REFERENCES = """def check_references(payload):
    sources = {source['source_id']: {segment['segment_id']: segment['text'] for segment in source['segments']} for source in payload['sources']}
    rows = []
    for field in payload['fields']:
        refs = []
        for index, reference in enumerate(field['references']):
            source = sources.get(reference['source_id'])
            if source is None:
                status = 'source_missing'
            elif reference['segment_id'] not in source:
                status = 'segment_missing'
            else:
                text = source[reference['segment_id']]
                start, end = reference['start'], reference['end']
                if not 0 <= start < end <= len(text):
                    status = 'invalid_range'
                elif text[start:end] != reference['quote']:
                    status = 'quote_mismatch'
                else:
                    status = 'valid'
            refs.append({'index': index, 'status': status})
        status = 'missing_reference' if not refs else 'valid' if all(ref['status'] == 'valid' for ref in refs) else 'invalid_reference'
        rows.append({'field_id': field['field_id'], 'status': status, 'references': refs})
    return {'fields': rows, 'valid': all(row['status'] == 'valid' for row in rows)}
"""


def baseline(task):
    return {'document_field_completeness': FIELD, 'document_source_references': REFERENCES}[task]
