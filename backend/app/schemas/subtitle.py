from pydantic import BaseModel


class SubtitleUpload(BaseModel):
    target_duration: int = 15
