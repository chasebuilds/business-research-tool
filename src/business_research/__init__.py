"""Company research datasets with a source, a date and a status on every fact."""

from business_research.models import Candidate, CompanyInput, Fact
from business_research.pipeline import research

__version__ = "0.1.0"
__all__ = ["Candidate", "CompanyInput", "Fact", "research", "__version__"]
