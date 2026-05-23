def _format_status_label(req_status: int | None, media_status: int | None) -> str:
    status_parts = []
    if req_status == 1:
        status_parts.append("pending")
    elif req_status == 2:
        status_parts.append("approved")
    elif req_status == 3:
        status_parts.append("declined")

    if media_status == 2:
        status_parts.append("pending download")
    elif media_status == 3:
        status_parts.append("processing")
    elif media_status == 4:
        status_parts.append("partially available")
    elif media_status == 5:
        status_parts.append("available")

    return ", ".join(status_parts) if status_parts else "unknown"
