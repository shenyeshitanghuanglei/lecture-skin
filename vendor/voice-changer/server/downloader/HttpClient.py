import certifi
import ssl
import aiohttp

CA_FILES = certifi.where()

class HttpClient:
    _instance = None

    def __init__(self):
        self.session: aiohttp.ClientSession = None

    @classmethod
    async def get_client(cls) -> aiohttp.ClientSession:
        if cls._instance is None:
            cls._instance = cls()
            ssl_context = ssl.create_default_context(cafile=CA_FILES)
            conn = aiohttp.TCPConnector(ssl_context=ssl_context)
            # voice-translater: trust_env=True honours HTTP(S)_PROXY so downloads
            # go through the user's local proxy instead of timing out.
            cls._instance.session = aiohttp.ClientSession(connector=conn, trust_env=True)
        return cls._instance.session
