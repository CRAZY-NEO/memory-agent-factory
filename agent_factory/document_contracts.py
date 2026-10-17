"""Portable typed document inputs/outputs and structural result validation.

No algorithm implementation or labelled evaluation data is included here.
"""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool, StrictInt, model_validator


class ReviewModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Record(ReviewModel):
    record_id: str = Field(min_length=1)
    fields: dict[str, JsonValue]


class FieldInput(ReviewModel):
    records: list[Record]
    required_fields: list[str]

    @model_validator(mode='after')
    def unique(self):
        ids = [row.record_id for row in self.records]
        if len(ids) != len(set(ids)) or len(self.required_fields) != len(set(self.required_fields)):
            raise ValueError('Record IDs and required fields must be unique')
        if any(not name for name in self.required_fields):
            raise ValueError('Required field names must be nonempty literal keys')
        return self


class Segment(ReviewModel):
    segment_id: str = Field(min_length=1)
    text: str


class Source(ReviewModel):
    source_id: str = Field(min_length=1)
    segments: list[Segment]

    @model_validator(mode='after')
    def unique(self):
        ids = [row.segment_id for row in self.segments]
        if len(ids) != len(set(ids)):
            raise ValueError('Segment IDs must be unique within a source')
        return self


class Reference(ReviewModel):
    source_id: str
    segment_id: str
    start: StrictInt
    end: StrictInt
    quote: str


class CitedField(ReviewModel):
    field_id: str = Field(min_length=1)
    references: list[Reference]


class ReferenceInput(ReviewModel):
    sources: list[Source]
    fields: list[CitedField]

    @model_validator(mode='after')
    def unique(self):
        for ids in ([row.source_id for row in self.sources], [row.field_id for row in self.fields]):
            if len(ids) != len(set(ids)):
                raise ValueError('Source IDs and field IDs must be unique')
        return self


class FieldResult(ReviewModel):
    record_id: str
    missing_fields: list[str]
    complete: StrictBool


class FieldOutput(ReviewModel):
    records: list[FieldResult]
    complete: StrictBool


class FormattedFieldInput(FieldInput):
    field_formats: dict[str, Literal['email']] = Field(default_factory=dict)

    @model_validator(mode='after')
    def format_keys(self):
        if any(not name for name in self.field_formats):
            raise ValueError('Format field names must be nonempty literal keys')
        return self


class FormattedFieldResult(FieldResult):
    invalid_fields: list[str]
    valid: StrictBool


class FormattedFieldOutput(ReviewModel):
    records: list[FormattedFieldResult]
    complete: StrictBool
    valid: StrictBool


class ReferenceResult(ReviewModel):
    index: StrictInt
    status: Literal['valid', 'source_missing', 'segment_missing', 'invalid_range', 'quote_mismatch']


class CitedFieldResult(ReviewModel):
    field_id: str
    status: Literal['valid', 'missing_reference', 'invalid_reference']
    references: list[ReferenceResult]


class ReferenceOutput(ReviewModel):
    fields: list[CitedFieldResult]
    valid: StrictBool


FIELD_BEHAVIORS = [
    'Implement check_fields(payload). payload has records [{record_id, fields}] and required_fields [str]. '
    'Field names are literal top-level keys, including dots; never split a dotted name into a path.',
    'A required value is missing only if its key is absent, its value is None, a string whose strip() is empty, '
    'or an empty list or dict. Numeric zero and boolean False are present. Nonempty lists/dicts are present '
    'without recursively checking their contents. Do not treat every falsy value as missing.',
    'Return {records: [{record_id, missing_fields, complete}], complete}. Preserve record order and '
    'required_fields order in missing_fields. Each record complete iff no missing fields; overall complete '
    'iff all records complete. Empty records yields records=[] and complete=True; no required fields '
    'makes every record complete. Never mutate inputs. Return only these exact keys.',
]
REFERENCE_BEHAVIORS = [
    'Implement check_references(payload). payload.sources is [{source_id, segments:[{segment_id,text}]}]. '
    'payload.fields is [{field_id,references:[{source_id,segment_id,start,end,quote}]}]. '
    'Source IDs and field IDs are unique; segment IDs are unique only WITHIN each source. '
    'Resolve segment within its cited source, not globally.',
    'For EACH reference preserve its zero-based index and choose status in strict precedence: '
    'source_missing if source ID absent; else segment_missing if segment ID absent within that source; '
    'else invalid_range unless 0 <= start < end <= len(text); else quote_mismatch unless '
    'text[start:end] == quote exactly; else valid. Offsets use Python Unicode character indexes, '
    'end-exclusive, not UTF-8 bytes. Do not strip, casefold, normalize or search for a quote elsewhere.',
    'Return {fields:[{field_id,status,references:[{index,status}]}], valid}. Preserve field/reference '
    'order and multiplicity. Field status missing_reference for empty references, otherwise valid iff '
    'ALL its references are valid, else invalid_reference. Overall valid iff ALL fields valid; empty '
    'fields gives fields=[] and valid=True. Return only exact keys and never mutate inputs.',
    'This checks citation locations and exact quotations, NOT whether a claim is semantically supported '
    'or factually true. A valid quote does not establish semantic truth.',
]

EMAIL_BEHAVIORS = [
    'Contract version 2 extends field completeness. payload also accepts optional field_formats, '
    'an insertion-ordered mapping of literal field names to "email", default {}. '
    'For each mapped field skip absent or missing values using the same missing-value definition. '
    'For present values, only strings matching re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\\.[A-Za-z0-9-]+)+", value) '
    'are valid emails. Do not strip or normalize; non-string present values are invalid. '
    'This is a deliberately bounded email syntax rule, not RFC validation or deliverability checking.',
    'Version 2 output records have exactly record_id, missing_fields, complete, invalid_fields, valid. '
    'invalid_fields follows field_formats insertion order. valid is complete AND no invalid_fields. '
    'Overall output has exactly records, complete, valid; overall valid iff all record valid flags. '
    'Keep completeness semantics unchanged. With no field_formats, invalid_fields=[] and valid=complete. '
    'This is an explicit output-schema version change; version 1 callers must retain their old binding.',
]

MODELS = {
    'document_field_completeness': (FieldInput, FieldOutput),
    'document_source_references': (ReferenceInput, ReferenceOutput),
}


def models_for(task, version='1'):
    if str(version) == '2' and task == 'document_field_completeness':
        return FormattedFieldInput, FormattedFieldOutput
    if str(version) != '1':
        raise ValueError('Unknown portable contract version')
    return MODELS[task]


def parse_input(task, payload, version='1'):
    json.dumps(payload, allow_nan=False)
    return models_for(task, version)[0].model_validate(payload).model_dump()


def check_output(task, result, payload, version='1'):
    normalized = models_for(task, version)[1].model_validate(result).model_dump()
    if task == 'document_field_completeness':
        if [row['record_id'] for row in normalized['records']] != [row['record_id'] for row in payload['records']]:
            raise ValueError('Output must preserve every record ID and order')
        for row in normalized['records']:
            if len(row['missing_fields']) != len(set(row['missing_fields'])) or any(
                    key not in payload['required_fields'] for key in row['missing_fields']):
                raise ValueError('Missing fields must be unique declared required keys')
            if row['missing_fields'] != [key for key in payload['required_fields'] if key in row['missing_fields']]:
                raise ValueError('Missing fields must follow required-fields order')
            if row['complete'] != (not row['missing_fields']):
                raise ValueError('Record complete flag conflicts with missing fields')
        if normalized['complete'] != all(row['complete'] for row in normalized['records']):
            raise ValueError('Overall complete flag conflicts with records')
        if str(version) == '2':
            formats = payload.get('field_formats', {})
            for row in normalized['records']:
                invalid = row['invalid_fields']
                if len(invalid) != len(set(invalid)) or invalid != [key for key in formats if key in invalid]:
                    raise ValueError('Invalid fields must be unique format keys in declared order')
                if any(key not in formats for key in invalid) or row['valid'] != (row['complete'] and not invalid):
                    raise ValueError('Validity flag conflicts with invalid/missing fields')
            if normalized['valid'] != all(row['valid'] for row in normalized['records']):
                raise ValueError('Overall valid flag conflicts with records')
    else:
        if [row['field_id'] for row in normalized['fields']] != [row['field_id'] for row in payload['fields']]:
            raise ValueError('Output must preserve every field ID and order')
        for row, original in zip(normalized['fields'], payload['fields']):
            if [ref['index'] for ref in row['references']] != list(range(len(original['references']))):
                raise ValueError('Output must preserve every reference index and order')
            status = ('missing_reference' if not original['references'] else
                      'valid' if all(ref['status'] == 'valid' for ref in row['references']) else 'invalid_reference')
            if row['status'] != status:
                raise ValueError('Field status conflicts with reference statuses')
        if normalized['valid'] != all(row['status'] == 'valid' for row in normalized['fields']):
            raise ValueError('Overall valid flag conflicts with fields')
    return normalized
