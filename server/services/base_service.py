class SingletonService:
    _instance = None

    def __new__(cls, *_args, **_kwargs):
        if cls._instance is None:
            cls._instance = super(SingletonService, cls).__new__(cls)
            cls._instance._initialized = False
            cls._instance._started = False
        return cls._instance

    async def ensure_initialized(self, *args, **kwargs):
        if not self._started:
            self._started = True
            await self.initialize(*args, **kwargs)
        return self
