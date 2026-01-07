class CoreError(RuntimeError):
    """Erro base do core (não depende de CLI/GUI)."""


class FetchError(CoreError):
    pass


class ParseError(CoreError):
    pass


class StorageError(CoreError):
    pass
