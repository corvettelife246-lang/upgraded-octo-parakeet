"""ML agent — model design, training pipelines, data analysis, and DL architecture."""
from typing import Optional

from agents.base_agent import BaseAgent
from config.settings import DEFAULT_MODEL

_SYSTEM = """You are the ML Agent — an expert in machine learning, deep learning, and data science.

Capabilities:
- Design neural network architectures (CNNs, RNNs, Transformers, etc.)
- Write complete PyTorch and TensorFlow training pipelines
- Perform data analysis and preprocessing
- Recommend hyperparameters and optimization strategies
- Evaluate models and interpret metrics
- Implement custom loss functions, layers, and training loops
- Guide fine-tuning of LLMs and diffusion models

Always provide complete, executable code with clear training loops and evaluation steps.
"""


class MLAgent(BaseAgent):
    name = "ML Agent"
    description = "Machine learning model design, training, and data analysis."

    def _system_prompt(self) -> str:
        return _SYSTEM

    async def run(self, prompt: str, context: Optional[dict] = None) -> str:
        history = (context or {}).get("history", [])
        messages = [*history, {"role": "user", "content": prompt}]
        return await self.llm.complete_async(
            messages, system=_SYSTEM, model=DEFAULT_MODEL, max_tokens=8192
        )

    async def design_architecture(self, task_description: str) -> str:
        prompt = (
            f"Design a complete neural network architecture for:\n{task_description}\n\n"
            "Include: architecture diagram (ASCII), layer specs, parameter counts, "
            "and a full PyTorch implementation."
        )
        return await self.run(prompt)

    async def write_training_pipeline(self, model_name: str, dataset_info: str) -> str:
        prompt = (
            f"Write a complete PyTorch training pipeline for {model_name}.\n"
            f"Dataset info: {dataset_info}\n"
            "Include: data loading, augmentation, training loop, validation, checkpointing, and logging."
        )
        return await self.run(prompt)
