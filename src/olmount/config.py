# src/olmount/config.py
from __future__ import annotations
import os, sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib
import tomli_w

CONFIG_PATH = Path(os.environ.get("OLMOUNT_CONFIG",
                       os.path.expanduser("~/.config/olmount/config.toml")))

@dataclass
class ServerProfile:
    name: str
    url: str
    cookie: str = ""
    csrf: str = ""
    user_id: str = ""
    email: str = ""

@dataclass
class Config:
    default: str = ""
    servers: dict[str, ServerProfile] = field(default_factory=dict)

    @classmethod
    def load(cls) -> "Config":
        if CONFIG_PATH.is_file():
            with CONFIG_PATH.open("rb") as f:
                data = tomllib.load(f)
            cfg = cls(default=data.get("default_server", ""))
            for name, s in data.get("servers", {}).items():
                cfg.servers[name] = ServerProfile(name=name, **s)
            return cfg
        return cls()

    def save(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {"default_server": self.default,
                "servers": {n: {k: v for k, v in asdict(s).items() if k != "name"}
                            for n, s in self.servers.items()}}
        with CONFIG_PATH.open("wb") as f:
            tomli_w.dump(data, f)

    def set_server(self, name, **fields) -> None:
        if name in self.servers:
            for k, v in fields.items(): setattr(self.servers[name], k, v)
        else:
            self.servers[name] = ServerProfile(name=name, **fields)

    def server(self, name) -> ServerProfile:
        if name not in self.servers: raise KeyError(name)
        return self.servers[name]

    def default_server(self) -> str:
        return self.default or (next(iter(self.servers)) if self.servers else "")

    def set_default(self, name) -> None:
        if name not in self.servers: raise KeyError(name)
        self.default = name
