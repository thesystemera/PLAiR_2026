class SingletonService:
    _instance = None

    def __new__(cls, *_args, **_kwargs):
        if cls._instance is None:
            cls._instance = super(SingletonService, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance