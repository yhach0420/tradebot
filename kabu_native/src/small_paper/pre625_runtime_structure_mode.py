"""

Phase612A → Phase616: pre-625 runtime structure mode delegates to CoreRuntimeMode.CORE_ONLY.

"""



from __future__ import annotations



import os

from typing import Any, Optional



from small_paper.config import SmallPaperPilotConfig

from small_paper.core_runtime_mode import (

    EXTENSION_FLAGS_OFF,

    CoreRuntimeMode,

    apply_core_runtime_mode,

    finalize_core_runtime_config,

    log_core_runtime_mode,

)



PRE625_RUNTIME_STRUCTURE_OFF = EXTENSION_FLAGS_OFF

STARTUP_LOG_LINE = "pre625_runtime_structure_mode=true"





def env_pre625_runtime_structure_mode_enabled() -> bool:

    val = os.environ.get("PRE625_RUNTIME_STRUCTURE_MODE", "").strip().lower()

    return val in ("1", "true", "yes", "on")





def resolve_pre625_runtime_structure_mode(

    *,

    yaml_flag: bool = False,

    cli_flag: bool = False,

    env_flag: Optional[bool] = None,

) -> bool:

    if cli_flag or yaml_flag:

        return True

    if env_flag is not None:

        return bool(env_flag)

    return env_pre625_runtime_structure_mode_enabled()





def apply_pre625_runtime_structure_mode(config: SmallPaperPilotConfig) -> SmallPaperPilotConfig:

    return apply_core_runtime_mode(config, CoreRuntimeMode.CORE_ONLY)





def finalize_runtime_structure_config(

    config: SmallPaperPilotConfig,

    *,

    cli_flag: bool = False,

) -> SmallPaperPilotConfig:

    return finalize_core_runtime_config(config, cli_pre625=resolve_pre625_runtime_structure_mode(cli_flag=cli_flag))





def pre625_runtime_structure_session_fields(config: SmallPaperPilotConfig) -> dict[str, Any]:

    from small_paper.core_runtime_mode import core_runtime_session_fields



    fields = core_runtime_session_fields(config)

    if fields.get("core_runtime_mode") == CoreRuntimeMode.CORE_ONLY.value:

        fields["pre625_runtime_structure_forced_off"] = dict(PRE625_RUNTIME_STRUCTURE_OFF)

    return fields





def log_pre625_runtime_structure_mode(config: SmallPaperPilotConfig) -> None:

    if bool(getattr(config, "pre625_runtime_structure_mode", False)):

        print(f"[PAPER TRADE] {STARTUP_LOG_LINE}", flush=True)

    log_core_runtime_mode(config)


