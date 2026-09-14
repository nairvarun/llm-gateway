from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def database_engine(url: str, schema: str = "public") -> AsyncEngine:
    return create_async_engine(
        url,
        echo=False,
        hide_parameters=True,
        pool_pre_ping=True,
        connect_args={"server_settings": {"search_path": schema}, "timeout": 3},
    )
