"""应用配置。

全部配置项支持通过环境变量（或 `.env` 文件）覆盖，字段名对应大写环境变量名，
例如 `database_url` 对应 `DATABASE_URL`。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 数据库（默认值仅本地演示，生产环境请通过环境变量覆盖）
    database_url: str = "postgresql+asyncpg://localhost/knowledge2exam"

    # 双 JWT
    jwt_secret: str = "dev-secret-key-change-me-in-production-1234567890"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30

    # Cookie 认证配置（替代双 JWT 的 Bearer 传输）
    cookie_secure: bool = False  # 生产环境必须设为 True（仅 HTTPS 传输）
    cookie_samesite: str = "lax"  # 允许顶级导航，防止跨站 CSRF
    cookie_domain: str | None = None  # 生产环境可配置为域名（如 .example.com）
    cookie_access_token_max_age: int = 15 * 60  # 与 access_token_expire_minutes 同步
    cookie_refresh_token_max_age: int = 30 * 24 * 60 * 60  # 30 天

    # 对象存储（首版为本地文件系统）
    storage_dir: Path = Path("./storage")

    # 上传限制
    max_upload_size_bytes: int = 50 * 1024 * 1024  # 50 MB

    # CORS
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # 模拟 pipeline 的步进间隔（秒），用于本地演示 SSE 进度
    mock_stage_delay_seconds: float = 0.5

    # LLM（OpenAI 兼容协议）
    llm_api_key: str = ""
    llm_base_url: str = "http://localhost:8000/v1"

    # MainAgent 配置
    agent_model: str = "gpt-4o"
    agent_temperature: float = 0.7
    agent_recursion_limit: int = 100  # ReAct 循环最大步数，防止失控
    agent_max_retries: int = 3  # 单题校验失败重试上限，超过则放弃该题

    # Skill system（ReAct agent 的技能目录）
    skills_dir: Path = Path("./skills")

    # 嵌入模型（OpenAI 兼容 embeddings 协议）
    embedding_api_key: str = ""
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    # 切块参数
    chunk_size: int = 700
    chunk_overlap: int = 100

    # 用户文本输入长度限制（字符数），超过则触发压缩
    max_keypoint_list_chars: int = 3000
    max_extra_requirement_chars: int = 2000

    # OCR（视觉 LLM，复用 LLM 配置或独立配置）
    ocr_model: str = "gpt-4o"

    # 旧格式转换工具路径（可选）
    libreoffice_path: str = "libreoffice"

    # Debug 模式（仅开发环境使用）
    debug_mode: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
