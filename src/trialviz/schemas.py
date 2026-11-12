"""Public request schema.

Provisional: the structured hint fields will be finalized with the question taxonomy.
"""

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500, description="Natural-language question.")
    drug_name: str | None = Field(default=None, max_length=200)
    condition: str | None = Field(default=None, max_length=200)
    sponsor: str | None = Field(default=None, max_length=200)
    country: str | None = Field(default=None, max_length=100)
    start_year: int | None = Field(default=None, ge=1900, le=2100)
    end_year: int | None = Field(default=None, ge=1900, le=2100)

    def hints(self) -> dict[str, str | int]:
        """Structured fields the caller set, without the free-text query."""
        return self.model_dump(exclude={"query"}, exclude_none=True)
