from pydantic import BaseModel, Field


class ThreadsDraftRequest(BaseModel):
    keyword: str = ""
    product_name: str = ""
    affiliate_url: str = ""
    product_url: str = ""
    price: str = ""
    shop_name: str = ""
    commission_rate: str = ""
    style: str = "natural"


class ThreadsDraft(BaseModel):
    content: str
    cta: str
    hashtags: list[str] = Field(default_factory=list)
    quality_score: int = 0


class AnalyticsTopPost(BaseModel):
    post_id: int
    keyword: str
    clicks: int


class AnalyticsSummary(BaseModel):
    total_posts: int
    draft: int
    needs_link: int
    approved: int
    posted: int
    total_clicks: int
    top_posts: list[AnalyticsTopPost]
