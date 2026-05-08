from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExportStage:
    key: str
    label: str
    weight: float


@dataclass(frozen=True)
class ExportProgressSnapshot:
    overall_progress: float
    stage_key: str
    stage_label: str
    stage_progress: float
    stage_index: int
    stage_count: int
    message: str


DEFAULT_STAGES: tuple[ExportStage, ...] = (
    ExportStage("detect_subject", "Detectando sujeito", 0.08),
    ExportStage("analyze_audio", "Analisando audio", 0.10),
    ExportStage("cut_segments", "Montando timeline", 0.34),
    ExportStage("render_effects", "Renderizando efeitos", 0.24),
    ExportStage("process_audio", "Processando audio", 0.12),
    ExportStage("generate_thumbnails", "Gerando thumbnails", 0.08),
    ExportStage("finalize", "Finalizando exportacao", 0.04),
)


class ExportProgressTracker:
    def __init__(self, stages: tuple[ExportStage, ...] = DEFAULT_STAGES) -> None:
        self.stages = stages
        self._index = {stage.key: idx for idx, stage in enumerate(stages)}

    def snapshot(
        self,
        stage_key: str,
        stage_progress: float,
        message: str,
    ) -> ExportProgressSnapshot:
        idx = self._index[stage_key]
        stage_progress = max(0.0, min(1.0, float(stage_progress)))
        completed = sum(stage.weight for stage in self.stages[:idx])
        current_weight = self.stages[idx].weight * stage_progress
        overall = completed + current_weight
        total = sum(stage.weight for stage in self.stages) or 1.0
        stage = self.stages[idx]
        return ExportProgressSnapshot(
            overall_progress=max(0.0, min(1.0, overall / total)),
            stage_key=stage.key,
            stage_label=stage.label,
            stage_progress=stage_progress,
            stage_index=idx + 1,
            stage_count=len(self.stages),
            message=message,
        )
