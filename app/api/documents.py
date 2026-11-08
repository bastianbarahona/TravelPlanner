from fastapi import APIRouter, Depends, Response

from app.api.deps import trip_editor, trip_member
from app.db.session import get_session
from app.services import documents

# Reads for every participant, writes for the organizer and editors. A new route joins one and inherits its checks.
readers = APIRouter(prefix="/trips/{tripId}/documents", dependencies=[Depends(trip_member)])
editors = APIRouter(prefix="/trips/{tripId}/documents", dependencies=[Depends(trip_editor)])


@editors.post("", status_code=202)
def request_document(response: Response, access=Depends(trip_editor), session=Depends(get_session)):
    """202 when this request created the document, 200 with the stored one when the source matches a document."""
    created, document = documents.create(session, access)
    if not created:
        response.status_code = 200
    return document


@readers.get("")
def list_documents(access=Depends(trip_member), session=Depends(get_session)):
    return documents.list_documents(session, access)


@readers.get("/{documentId}")
def get_document(documentId: str, access=Depends(trip_member), session=Depends(get_session)):
    return documents.get_document(session, access, documentId)


@readers.get("/{documentId}/content")
def download_document(documentId: str, access=Depends(trip_member), session=Depends(get_session)):
    content, filename = documents.read_content(session, access, documentId)
    return Response(
        content,
        media_type="text/html; charset=utf-8",
        headers={
            "content-disposition": f'attachment; filename="{filename}"',
            "content-security-policy": "default-src 'none'",
            "x-content-type-options": "nosniff",
        },
    )


@editors.post("/{documentId}/retry")
def retry_document(documentId: str, access=Depends(trip_editor), session=Depends(get_session)):
    return documents.retry(session, access, documentId)
