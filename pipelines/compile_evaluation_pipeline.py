from pathlib import Path

from kfp import compiler

from vertex_evaluation_pipeline import caffemate_evaluation_pipeline


OUTPUT = Path(__file__).parent / "compiled" / "caffemate-operational-evaluation.json"
OUTPUT.parent.mkdir(parents=True, exist_ok=True)
compiler.Compiler().compile(
    pipeline_func=caffemate_evaluation_pipeline,
    package_path=str(OUTPUT),
)
print(OUTPUT)
