def check_references(payload):
    sources = payload.get("sources", [])
    fields = payload.get("fields", [])

    source_map = {}
    for source in sources:
        source_id = source.get("source_id")
        segments = source.get("segments", [])
        segment_map = {}
        for segment in segments:
            segment_id = segment.get("segment_id")
            text = segment.get("text", "")
            segment_map[segment_id] = text
        source_map[source_id] = segment_map

    field_results = []
    all_valid = True

    for field in fields:
        field_id = field.get("field_id")
        references = field.get("references", [])
        reference_results = []
        field_valid = True

        if not references:
            field_status = "missing_reference"
            field_valid = False
        else:
            for index, reference in enumerate(references):
                source_id = reference.get("source_id")
                segment_id = reference.get("segment_id")
                start = reference.get("start")
                end = reference.get("end")
                quote = reference.get("quote", "")

                if source_id not in source_map:
                    status = "source_missing"
                else:
                    segment_map = source_map[source_id]
                    if segment_id not in segment_map:
                        status = "segment_missing"
                    else:
                        text = segment_map[segment_id]
                        if not (isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(text)):
                            status = "invalid_range"
                        elif text[start:end] != quote:
                            status = "quote_mismatch"
                        else:
                            status = "valid"

                reference_results.append({"index": index, "status": status})
                if status != "valid":
                    field_valid = False

            field_status = "valid" if field_valid else "invalid_reference"

        if field_status != "valid":
            all_valid = False

        field_results.append({
            "field_id": field_id,
            "status": field_status,
            "references": reference_results
        })

    return {
        "fields": field_results,
        "valid": all_valid
    }
