def check_fields(payload):
    records = payload["records"]
    required_fields = payload["required_fields"]
    results = []
    all_complete = True
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
        results.append({
            "record_id": record["record_id"],
            "missing_fields": missing_fields,
            "complete": record_complete
        })
    return {
        "records": results,
        "complete": all_complete
    }