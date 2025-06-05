# Copyright (c) 2024-2025 Alain Prasquier - Supervaize.com. All rights reserved.
#
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
# If a copy of the MPL was not distributed with this file, you can obtain one at
# https://mozilla.org/MPL/2.0/.

# Copyright (c) 2024-2025 Alain Prasquier - Supervaize.com. All rights reserved.
#
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
# If a copy of the MPL was not distributed with this file, You can obtain one at
# https://mozilla.org/MPL/2.0/.

import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Generic, List, Optional, TypeVar

from tinydb import Query, TinyDB
from tinydb.middlewares import CachingMiddleware
from tinydb.storages import JSONStorage

from supervaizer.common import log, singleton
from supervaizer.lifecycle import WorkflowEntity

if TYPE_CHECKING:
    from supervaizer.case import Case
    from supervaizer.job import Job
    from supervaizer.lifecycle import EntityEvents, EntityStatus

T = TypeVar("T", bound=WorkflowEntity)

DATA_STORAGE_PATH = os.getenv("DATA_STORAGE_PATH", "./data")


@singleton
class StorageManager:
    """
    Thread-safe TinyDB-based persistence manager for WorkflowEntity instances.

    Stores entities in separate tables by type, with foreign key relationships
    represented as ID references (Job.case_ids, Case.job_id).
    """

    def __init__(self, db_path: str = f"{DATA_STORAGE_PATH}/entities.json"):
        """
        Initialize the storage manager.

        Args:
            db_path: Path to the TinyDB JSON file
        """
        self.db_path = db_path
        self._lock = threading.Lock()

        # Ensure data directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # Initialize TinyDB with caching middleware for thread safety
        self._db = TinyDB(
            db_path, storage=CachingMiddleware(JSONStorage), sort_keys=True, indent=2
        )

        log.info(f"[StorageManager] DB initialized at {db_path}")

    def save_object(self, type: str, obj: Dict[str, Any]) -> None:
        """
        Save an object to the appropriate table.

        Args:
            type: The object type (class name)
            obj: Dictionary representation of the object
        """
        with self._lock:
            table = self._db.table(type)
            obj_id = obj.get("id")

            if not obj_id:
                raise ValueError(f"Object must have an 'id' field: {obj}")

            # Use upsert to handle both new and existing objects
            query = Query()
            table.upsert(obj, query.id == obj_id)

            log.debug(f"Saved {type} object with ID: {obj_id}")

    def get_objects(self, type: str) -> List[Dict[str, Any]]:
        """
        Get all objects of a specific type.

        Args:
            type: The object type (class name)

        Returns:
            List of object dictionaries
        """
        with self._lock:
            table = self._db.table(type)
            return table.all()

    def get_object_by_id(self, type: str, obj_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific object by its ID.

        Args:
            type: The object type (class name)
            obj_id: The object ID

        Returns:
            Object dictionary if found, None otherwise
        """
        with self._lock:
            table = self._db.table(type)
            query = Query()
            result = table.search(query.id == obj_id)
            return result[0] if result else None

    def delete_object(self, type: str, obj_id: str) -> bool:
        """
        Delete an object by its ID.

        Args:
            type: The object type (class name)
            obj_id: The object ID

        Returns:
            True if object was deleted, False if not found
        """
        with self._lock:
            table = self._db.table(type)
            query = Query()
            deleted_count = len(table.remove(query.id == obj_id))

            if deleted_count > 0:
                log.debug(f"Deleted {type} object with ID: {obj_id}")
                return True
            return False

    def reset_storage(self) -> None:
        """
        Reset storage by clearing all tables but preserving the database file.
        """
        with self._lock:
            # Clear all tables
            for table_name in self._db.tables():
                self._db.drop_table(table_name)

            log.info("Storage reset - all tables cleared")

    def get_cases_for_job(self, job_id: str) -> List[Dict[str, Any]]:
        """
        Helper method to get all cases for a specific job.

        Args:
            job_id: The job ID

        Returns:
            List of case dictionaries
        """
        with self._lock:
            table = self._db.table("Case")
            query = Query()
            return table.search(query.job_id == job_id)

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            if hasattr(self, "_db") and self._db is not None:
                try:
                    if hasattr(self._db, "close"):
                        self._db.close()
                    log.info("StorageManager database closed")
                except ValueError as e:
                    # Handle the case where the file is already closed
                    if "I/O operation on closed file" in str(e):
                        log.debug("Database file already closed")
                    else:
                        raise


class EntityRepository(Generic[T]):
    """
    Generic repository for WorkflowEntity types with type-safe operations.

    Provides higher-level abstraction over StorageManager for specific entity types.
    """

    def __init__(
        self, entity_class: type[T], storage_manager: Optional[StorageManager] = None
    ):
        """
        Initialize repository for a specific entity type.

        Args:
            entity_class: The entity class this repository manages
            storage_manager: Optional storage manager instance
        """
        self.entity_class = entity_class
        self.type_name = entity_class.__name__
        self.storage = storage_manager or StorageManager()

    def get_by_id(self, entity_id: str) -> Optional[T]:
        """
        Get an entity by its ID.

        Args:
            entity_id: The entity ID

        Returns:
            Entity instance if found, None otherwise
        """
        data = self.storage.get_object_by_id(self.type_name, entity_id)
        if data:
            return self._from_dict(data)
        return None

    def save(self, entity: T) -> None:
        """
        Save an entity to storage.

        Args:
            entity: The entity to save
        """
        data = self._to_dict(entity)
        self.storage.save_object(self.type_name, data)

    def get_all(self) -> List[T]:
        """
        Get all entities of this type.

        Returns:
            List of entity instances
        """
        data_list = self.storage.get_objects(self.type_name)
        return [self._from_dict(data) for data in data_list]

    def delete(self, entity_id: str) -> bool:
        """
        Delete an entity by its ID.

        Args:
            entity_id: The entity ID

        Returns:
            True if deleted, False if not found
        """
        return self.storage.delete_object(self.type_name, entity_id)

    def _to_dict(self, entity: T) -> Dict[str, Any]:
        """Convert entity to dictionary using its to_dict property."""
        if hasattr(entity, "to_dict"):
            return dict(entity.to_dict)
        else:
            # Fallback for entities without to_dict
            return {
                field: getattr(entity, field)
                for field in entity.__dataclass_fields__
                if hasattr(entity, field)
            }

    def _from_dict(self, data: Dict[str, Any]) -> T:
        """
        Convert dictionary back to entity instance.

        Note: This is a simplified implementation. In practice, you might need
        more sophisticated deserialization depending on your entity structure.
        """
        # For entities inheriting from SvBaseModel (Pydantic), use model construction
        if hasattr(self.entity_class, "model_validate"):
            return self.entity_class.model_validate(data)  # type: ignore
        else:
            # Fallback for other types
            return self.entity_class(**data)


class PersistentEntityLifecycle:
    """
    Enhanced EntityLifecycle that automatically persists entity state changes.

    This class wraps the original EntityLifecycle methods to add persistence.
    """

    @staticmethod
    def transition(
        entity: T, to_status: "EntityStatus", storage: Optional[StorageManager] = None
    ) -> tuple[bool, str]:
        """
        Transition an entity and automatically persist the change.

        Args:
            entity: The entity to transition
            to_status: Target status
            storage: Optional storage manager instance

        Returns:
            Tuple of (success, error_message)
        """
        # Import here to avoid circular imports
        from supervaizer.lifecycle import EntityLifecycle

        # Perform the transition
        success, error = EntityLifecycle.transition(entity, to_status)

        # If successful, persist the entity
        if success:
            storage_mgr = storage or StorageManager()
            entity_dict = entity.to_dict if hasattr(entity, "to_dict") else vars(entity)
            storage_mgr.save_object(type(entity).__name__, entity_dict)
            log.debug(
                f"Auto-persisted {type(entity).__name__} {entity.id} after transition to {to_status}"
            )

        return success, error

    @staticmethod
    def handle_event(
        entity: T, event: "EntityEvents", storage: Optional[StorageManager] = None
    ) -> tuple[bool, str]:
        """
        Handle an event and automatically persist the change.

        Args:
            entity: The entity to handle event for
            event: The event to handle
            storage: Optional storage manager instance

        Returns:
            Tuple of (success, error_message)
        """
        # Import here to avoid circular imports
        from supervaizer.lifecycle import EntityLifecycle

        # Handle the event
        success, error = EntityLifecycle.handle_event(entity, event)

        # If successful, persist the entity
        if success:
            storage_mgr = storage or StorageManager()
            entity_dict = entity.to_dict if hasattr(entity, "to_dict") else vars(entity)
            storage_mgr.save_object(type(entity).__name__, entity_dict)
            log.debug(
                f"Auto-persisted {type(entity).__name__} {entity.id} after handling event {event}"
            )

        return success, error


def create_job_repository() -> "EntityRepository[Job]":
    """Factory function to create a Job repository."""
    from supervaizer.job import Job

    return EntityRepository(Job)


def create_case_repository() -> "EntityRepository[Case]":
    """Factory function to create a Case repository."""
    from supervaizer.case import Case

    return EntityRepository(Case)
