import re

def check_fields(payload):
    records = payload["records"]
    required_fields = payload["required_fields"]
    field_formats = payload.get("field_formats", {})

    output_records = []
    all_complete = True
    all_valid = True

    for record in records:
        fields = record["fields"]
        missing_fields = []
        for field_name in required_fields:
            if field_name not in fields:
                missing_fields.append(field_name)
                continue
            value = fields[field_name]
            if value is None:
                missing_fields.append(field_name)
            elif isinstance(value, str) and value.strip() == "":
                missing_fields.append(field_name)
            elif isinstance(value, list) and len(value) == 0:
                missing_fields.append(field_name)
            elif isinstance(value, dict) and len(value) == 0:
                missing_fields.append(field_name)

        complete = len(missing_fields) == 0

        invalid_fields = []
        for field_name in field_formats:
            if field_formats[field_name] != "email":
                continue
            if field_name in missing_fields:
                continue
            if field_name not in fields:
                continue
            value = fields[field_name]
            if value is None:
                continue
            if isinstance(value, str) and value.strip() == "":
                continue
            if isinstance(value, list) and len(value) == 0:
                continue
            if isinstance(value, dict) and len(value) == 0:
                continue
            if not isinstance(value, str):
                invalid_fields.append(field_name)
            elif not re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", value):
                invalid_fields.append(field_name)

        valid = complete and len(invalid_fields) == 0

        output_records.append({
            "record_id": record["record_id"],
            "missing_fields": missing_fields,
            "complete": complete,
            "invalid_fields": invalid_fields,
            "valid": valid
        })

        if not complete:
            all_complete = False
        if not valid:
            all_valid = False

    return {
        "records": output_records,
        "complete": all_complete,
        "valid": all_valid
    }
