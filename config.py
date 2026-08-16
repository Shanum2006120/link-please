from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    pseudogram_api_url: str = "https://pseudogram-api.onrender.com"
    api_key: str = ""
    
    class Config:
        env_file = ".env"

settings = Settings()
