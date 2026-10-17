import re

def check_fields(payload):
    records = payload["records"]
    required_fields = payload["required_fields"]
    field_formats = payload.get("field_formats", {})
    email_pattern = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
    results = []
    all_complete = True
    all_valid = True
    for record in records:
        fields = record["fields"]
        missing_fields = []
        for field_name in required_fields:
            if field_name not in fields:
                missing_fields.append(field_name)
            else:
                value = fields[field_name]
                if value is None:
                    missing_fields.append(field_name)
                elif isinstance(value, str) and value.strip() == "":
                    missing_fields.append(field_name)
                elif isinstance(value, (list, dict)) and len(value) == 0:
                    missing_fields.append(field_name)
        record_complete = len(missing_fields) == 0
        if not record_complete:
            all_complete = False
        invalid_fields = []
        for field_name in field_formats:
            if field_name in missing_fields:
                continue
            if field_name not in fields:
                continue
            value = fields[field_name]
            if value is None:
                continue
            if isinstance(value, str) and value.strip() == "":
                continue
            if isinstance(value, (list, dict)) and len(value) == 0:
                continue
            if not isinstance(value, str) or email_pattern.fullmatch(value) is None:
                invalid_fields.append(field_name)
        record_valid = record_complete and len(invalid_fields) == 0
        if not record_valid:
            all_valid = False
        results.append({
            "record_id": record["record_id"],
            "missing_fields": missing_fields,
            "complete": record_complete,
            "invalid_fields": invalid_fields,
            "valid": record_valid
        })
    return {
        "records": results,
        "complete": all_complete,
        "valid": all_valid
    }