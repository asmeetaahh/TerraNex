"""SQLAlchemy models.

Importing this package registers every model on `Base.metadata`, which is what Alembic
autogeneration and `create_all` both need. Import the package, not the modules, so a
new model cannot be left out of a migration by being forgotten here.
"""

from app.models.analysis import AnalysisRunORM
from app.models.base import Base
from app.models.crop import CropORM
from app.models.farm import FarmCropORM, FarmORM
from app.models.user import UserORM

__all__ = ["AnalysisRunORM", "Base", "CropORM", "FarmCropORM", "FarmORM", "UserORM"]
