from .context import ProviderPrivacyGate
from .context_v1 import ContextCompiler
from .models import (
    CognitiveContext,
    CognitiveJournalEntry,
    CognitiveRequest,
    CognitiveResponsePlan,
    ContextMemory,
    ConversationMessage,
    JournalStage,
    MessageSource,
    MessageStatus,
    SpeechSegment,
)
from .providers import CognitiveProvider, ProviderDelta, ProviderError
from .providers_identity import (
    CloudflareCognitiveProvider,
    GeminiCognitiveProvider,
    GroqCognitiveProvider,
    OpenRouterCognitiveProvider,
)
from .proposals import ActionSuggestion, BeliefHypothesisProposal, CognitiveProposalBundle, MemoryProposal
# Preserve the historical layered-runtime import as an explicit architectural
# dependency; the identity-aware subclass below only changes provider projection.
from .structured_runtime import CognitiveEngine
from .identity_runtime import CognitiveEngine as IdentityAwareCognitiveEngine
CognitiveEngine = IdentityAwareCognitiveEngine
from .replay import CognitiveReplayResult
from .speech import SpeechCue, SpeechNormalizer, SpeechPlan, SpeechRender, SpeechSegmenter
from .store_v1 import CognitiveStore

__all__ = [
    "ActionSuggestion",
    "BeliefHypothesisProposal",
    "CloudflareCognitiveProvider",
    "CognitiveContext",
    "CognitiveEngine",
    "CognitiveJournalEntry",
    "CognitiveProposalBundle",
    "CognitiveProvider",
    "CognitiveReplayResult",
    "CognitiveRequest",
    "CognitiveResponsePlan",
    "CognitiveStore",
    "ContextCompiler",
    "ContextMemory",
    "ConversationMessage",
    "GeminiCognitiveProvider",
    "GroqCognitiveProvider",
    "JournalStage",
    "MemoryProposal",
    "MessageSource",
    "MessageStatus",
    "OpenRouterCognitiveProvider",
    "ProviderDelta",
    "ProviderError",
    "ProviderPrivacyGate",
    "SpeechNormalizer",
    "SpeechCue",
    "SpeechPlan",
    "SpeechRender",
    "SpeechSegment",
    "SpeechSegmenter",
]
