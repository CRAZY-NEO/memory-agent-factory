def check_fields(payload):
    records = payload["records"]
    required_fields = payload["required_fields"]
    results = []
    all_complete = True
    for record in records:
        fields = record["fields"]
        missing = []
        for field_name in required_fields:
            if field_name not in fields:
                missing.append(field_name)
                continue
            value = fields[field_name]
            if value is None:
                missing.append(field_name)
            elif isinstance(value, str) and value.strip() == "":
                missing.append(field_name)
            elif isinstance(value, list) and len(value) == 0:
                missing.append(field_name)
            elif isinstance(value, dict) and len(value) == 0:
                missing.append(field_name)
        complete = len(missing) == 0
        if not complete:
            all_complete = False
        results.append({
            "record_id": record["record_id"],
            "missing_fields": missing,
            "complete": complete
        })
    return {
        "records": results,
        "complete": all_complete
    }