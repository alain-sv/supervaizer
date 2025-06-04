# Copyright (c) 2024-2025 Alain Prasquier - Supervaize.com. All rights reserved.
#
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
# If a copy of the MPL was not distributed with this file, You can obtain one at
# https://mozilla.org/MPL/2.0/.

import os
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

import pytest
from unittest.mock import MagicMock, patch

from supervaizer.storage import (
    StorageManager,
    EntityRepository,
    PersistentEntityLifecycle,
    create_job_repository,
    create_case_repository,
)
from supervaizer.lifecycle import EntityStatus, EntityEvents
from supervaizer.job import Job, JobContext, Jobs
from supervaizer.case import Case, CaseNode, CaseNoteType, Cases


class TestStorageManager:
    """Test the StorageManager class."""

    @pytest.fixture
    def temp_db_path(self):
        """Create a temporary database path for testing."""
        with tempfile.TemporaryDirectory() as temp_dir:
            yield os.path.join(temp_dir, "test_entities.json")

    @pytest.fixture
    def storage_manager(self, temp_db_path):
        """Create a StorageManager instance for testing."""
        # Clear the singleton instances to ensure fresh instance
        StorageManager._instances = {}
        storage = StorageManager(db_path=temp_db_path)
        storage.reset_storage()  # Ensure clean state
        return storage

    def test_storage_manager_init(self, temp_db_path):
        """Test StorageManager initialization."""
        storage = StorageManager(db_path=temp_db_path)

        assert storage.db_path == temp_db_path
        assert hasattr(storage, "_lock")
        assert hasattr(storage, "_db")
        assert Path(temp_db_path).parent.exists()

    def test_save_object(self, storage_manager):
        """Test saving an object."""
        test_obj = {"id": "test-123", "name": "Test Object", "status": "active"}

        storage_manager.save_object("TestType", test_obj)

        # Verify object was saved
        retrieved = storage_manager.get_object_by_id("TestType", "test-123")
        assert retrieved == test_obj

    def test_save_object_without_id(self, storage_manager):
        """Test saving an object without id raises error."""
        test_obj = {"name": "Test Object"}

        with pytest.raises(ValueError, match="Object must have an 'id' field"):
            storage_manager.save_object("TestType", test_obj)

    def test_get_objects(self, storage_manager):
        """Test getting all objects of a type."""
        test_objects = [
            {"id": "test-1", "name": "Object 1"},
            {"id": "test-2", "name": "Object 2"},
        ]

        for obj in test_objects:
            storage_manager.save_object("TestType", obj)

        retrieved = storage_manager.get_objects("TestType")
        assert len(retrieved) == 2
        assert retrieved[0] in test_objects
        assert retrieved[1] in test_objects

    def test_get_object_by_id(self, storage_manager):
        """Test getting a specific object by ID."""
        test_obj = {"id": "test-123", "name": "Test Object"}
        storage_manager.save_object("TestType", test_obj)

        retrieved = storage_manager.get_object_by_id("TestType", "test-123")
        assert retrieved == test_obj

        # Test non-existent object
        assert storage_manager.get_object_by_id("TestType", "non-existent") is None

    def test_delete_object(self, storage_manager):
        """Test deleting an object."""
        test_obj = {"id": "test-123", "name": "Test Object"}
        storage_manager.save_object("TestType", test_obj)

        # Verify object exists
        assert storage_manager.get_object_by_id("TestType", "test-123") is not None

        # Delete object
        result = storage_manager.delete_object("TestType", "test-123")
        assert result is True

        # Verify object is gone
        assert storage_manager.get_object_by_id("TestType", "test-123") is None

        # Test deleting non-existent object
        result = storage_manager.delete_object("TestType", "non-existent")
        assert result is False

    def test_reset_storage(self, storage_manager):
        """Test resetting storage."""
        # Add some test data
        storage_manager.save_object("Type1", {"id": "test-1", "name": "Object 1"})
        storage_manager.save_object("Type2", {"id": "test-2", "name": "Object 2"})

        # Verify data exists
        assert len(storage_manager.get_objects("Type1")) == 1
        assert len(storage_manager.get_objects("Type2")) == 1

        # Reset storage
        storage_manager.reset_storage()

        # Verify all data is gone
        assert len(storage_manager.get_objects("Type1")) == 0
        assert len(storage_manager.get_objects("Type2")) == 0

    def test_get_cases_for_job(self, storage_manager):
        """Test getting cases for a specific job."""
        cases = [
            {"id": "case-1", "job_id": "job-1", "name": "Case 1"},
            {"id": "case-2", "job_id": "job-1", "name": "Case 2"},
            {"id": "case-3", "job_id": "job-2", "name": "Case 3"},
        ]

        for case in cases:
            storage_manager.save_object("Case", case)

        job1_cases = storage_manager.get_cases_for_job("job-1")
        assert len(job1_cases) == 2
        assert all(case["job_id"] == "job-1" for case in job1_cases)

        job2_cases = storage_manager.get_cases_for_job("job-2")
        assert len(job2_cases) == 1
        assert job2_cases[0]["job_id"] == "job-2"

    def test_thread_safety(self, storage_manager):
        """Test thread safety of operations."""
        results = []
        errors = []

        def worker(worker_id):
            try:
                for i in range(10):
                    obj = {"id": f"worker-{worker_id}-{i}", "data": f"data-{i}"}
                    storage_manager.save_object("ThreadTest", obj)
                    time.sleep(0.001)  # Small delay to increase contention
                    retrieved = storage_manager.get_object_by_id(
                        "ThreadTest", obj["id"]
                    )
                    assert retrieved == obj
                results.append(f"worker-{worker_id}-success")
            except Exception as e:
                errors.append(f"worker-{worker_id}-error: {e}")

        # Start multiple threads
        threads = []
        for i in range(5):
            thread = threading.Thread(target=worker, args=(i,))
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Check results
        assert len(errors) == 0, f"Thread safety errors: {errors}"
        assert len(results) == 5

        # Verify all objects were saved
        all_objects = storage_manager.get_objects("ThreadTest")
        assert len(all_objects) == 50  # 5 workers * 10 objects each


class TestEntityRepository:
    """Test the EntityRepository class."""

    @pytest.fixture
    def temp_db_path(self):
        """Create a temporary database path for testing."""
        with tempfile.TemporaryDirectory() as temp_dir:
            yield os.path.join(temp_dir, "test_entities.json")

    @pytest.fixture
    def storage_manager(self, temp_db_path):
        """Create a StorageManager instance for testing."""
        # Clear the singleton instances to ensure fresh instance
        StorageManager._instances = {}
        storage = StorageManager(db_path=temp_db_path)
        storage.reset_storage()  # Ensure clean state
        return storage

    @pytest.fixture
    def mock_entity_class(self):
        """Create a mock entity class for testing."""

        class MockEntity:
            def __init__(self, id, name, status="active"):
                self.id = id
                self.name = name
                self.status = status

            @property
            def to_dict(self):
                return {"id": self.id, "name": self.name, "status": self.status}

            @classmethod
            def model_validate(cls, data: Dict[str, Any]):
                return cls(**data)

        return MockEntity

    def test_repository_init(self, storage_manager, mock_entity_class):
        """Test repository initialization."""
        repo = EntityRepository(mock_entity_class, storage_manager)

        assert repo.entity_class == mock_entity_class
        assert repo.type_name == "MockEntity"
        assert repo.storage == storage_manager

    def test_save_and_get_by_id(self, storage_manager, mock_entity_class):
        """Test saving and retrieving entities."""
        repo = EntityRepository(mock_entity_class, storage_manager)
        entity = mock_entity_class("test-123", "Test Entity")

        # Save entity
        repo.save(entity)

        # Retrieve entity
        retrieved = repo.get_by_id("test-123")
        assert retrieved is not None
        assert retrieved.id == entity.id
        assert retrieved.name == entity.name

    def test_get_all(self, storage_manager, mock_entity_class):
        """Test getting all entities."""
        repo = EntityRepository(mock_entity_class, storage_manager)
        entities = [
            mock_entity_class("entity-1", "Entity 1"),
            mock_entity_class("entity-2", "Entity 2"),
        ]

        # Save entities
        for entity in entities:
            repo.save(entity)

        # Get all entities
        retrieved = repo.get_all()
        assert len(retrieved) == 2
        assert all(isinstance(e, mock_entity_class) for e in retrieved)

    def test_delete(self, storage_manager, mock_entity_class):
        """Test deleting entities."""
        repo = EntityRepository(mock_entity_class, storage_manager)
        entity = mock_entity_class("test-123", "Test Entity")

        # Save entity
        repo.save(entity)
        assert repo.get_by_id("test-123") is not None

        # Delete entity
        result = repo.delete("test-123")
        assert result is True
        assert repo.get_by_id("test-123") is None

        # Delete non-existent entity
        result = repo.delete("non-existent")
        assert result is False


class TestPersistentEntityLifecycle:
    """Test the PersistentEntityLifecycle class."""

    @pytest.fixture
    def temp_db_path(self):
        """Create a temporary database path for testing."""
        with tempfile.TemporaryDirectory() as temp_dir:
            yield os.path.join(temp_dir, "test_entities.json")

    @pytest.fixture
    def storage_manager(self, temp_db_path):
        """Create a StorageManager instance for testing."""
        # Clear the singleton instances to ensure fresh instance
        StorageManager._instances = {}
        storage = StorageManager(db_path=temp_db_path)
        storage.reset_storage()  # Ensure clean state
        return storage

    @pytest.fixture
    def mock_entity(self):
        """Create a mock entity for testing."""
        entity = MagicMock()
        entity.id = "test-entity-123"
        entity.status = EntityStatus.STOPPED
        entity.to_dict = {"id": "test-entity-123", "status": "stopped"}
        entity.__class__.__name__ = "MockEntity"
        return entity

    @patch("supervaizer.lifecycle.EntityLifecycle")
    def test_persistent_transition(self, mock_lifecycle, storage_manager, mock_entity):
        """Test persistent transition method."""
        mock_lifecycle.transition.return_value = (True, "")

        success, error = PersistentEntityLifecycle.transition(
            mock_entity, EntityStatus.IN_PROGRESS, storage_manager
        )

        assert success is True
        assert error == ""
        mock_lifecycle.transition.assert_called_once_with(
            mock_entity, EntityStatus.IN_PROGRESS
        )

        # Verify entity was persisted
        stored = storage_manager.get_object_by_id("MockEntity", "test-entity-123")
        assert stored is not None

    @patch("supervaizer.lifecycle.EntityLifecycle")
    def test_persistent_handle_event(
        self, mock_lifecycle, storage_manager, mock_entity
    ):
        """Test persistent handle_event method."""
        mock_lifecycle.handle_event.return_value = (True, "")

        success, error = PersistentEntityLifecycle.handle_event(
            mock_entity, EntityEvents.START_WORK, storage_manager
        )

        assert success is True
        assert error == ""
        mock_lifecycle.handle_event.assert_called_once_with(
            mock_entity, EntityEvents.START_WORK
        )

        # Verify entity was persisted
        stored = storage_manager.get_object_by_id("MockEntity", "test-entity-123")
        assert stored is not None

    @patch("supervaizer.lifecycle.EntityLifecycle")
    def test_persistent_transition_failure(
        self, mock_lifecycle, storage_manager, mock_entity
    ):
        """Test persistent transition when underlying transition fails."""
        mock_lifecycle.transition.return_value = (False, "Invalid transition")

        success, error = PersistentEntityLifecycle.transition(
            mock_entity, EntityStatus.COMPLETED, storage_manager
        )

        assert success is False
        assert error == "Invalid transition"

        # Verify entity was NOT persisted
        stored = storage_manager.get_object_by_id("MockEntity", "test-entity-123")
        assert stored is None


class TestFactoryFunctions:
    """Test the factory functions."""

    def test_create_job_repository(self):
        """Test creating a job repository."""
        repo = create_job_repository()
        assert isinstance(repo, EntityRepository)
        assert repo.type_name == "Job"

    def test_create_case_repository(self):
        """Test creating a case repository."""
        repo = create_case_repository()
        assert isinstance(repo, EntityRepository)
        assert repo.type_name == "Case"


class TestIntegrationWithActualEntities:
    """Integration tests with actual Job and Case entities."""

    @pytest.fixture
    def temp_db_path(self):
        """Create a temporary database path for testing."""
        with tempfile.TemporaryDirectory() as temp_dir:
            yield os.path.join(temp_dir, "test_entities.json")

    @pytest.fixture
    def storage_manager(self, temp_db_path):
        """Create a StorageManager instance for testing."""
        # Clear the singleton instances to ensure fresh instance
        StorageManager._instances = {}
        storage = StorageManager(db_path=temp_db_path)
        storage.reset_storage()  # Ensure clean state
        return storage

    @pytest.fixture
    def job_context(self):
        """Create a job context for testing."""
        return JobContext(
            workspace_id="test-workspace",
            job_id="test-job-123",
            started_by="test-user",
            started_at=datetime.now(),
            mission_id="test-mission",
            mission_name="Test Mission",
        )

    def test_job_persistence(self, storage_manager, job_context):
        """Test persisting actual Job entities."""
        # Clear any existing jobs from singleton
        Jobs().__init__()

        job = Job(
            id="test-job-123",
            name="Test Job",
            agent_name="test-agent",
            status=EntityStatus.STOPPED,
            job_context=job_context,
        )

        # Save job to storage
        storage_manager.save_object("Job", job.to_dict)

        # Retrieve job data
        stored_data = storage_manager.get_object_by_id("Job", "test-job-123")
        assert stored_data is not None
        assert stored_data["id"] == "test-job-123"
        assert stored_data["name"] == "Test Job"
        assert stored_data["agent_name"] == "test-agent"
        assert stored_data["case_ids"] == []

    @patch("supervaizer.account_service.send_event")
    def test_case_persistence(self, mock_send_event, storage_manager):
        """Test persisting actual Case entities."""
        # Clear any existing cases from singleton
        Cases().__init__()

        # Mock the send_event service to avoid HTTP calls
        mock_send_event.return_value = MagicMock()

        # Create a proper Account instance for testing
        from supervaizer.account import Account

        account = Account(
            workspace_id="test-workspace",
            api_key="test-api-key",
            api_url="https://test.api.url",
        )

        case_nodes = [
            CaseNode(
                name="Test Node", description="Test Description", type=CaseNoteType.INFO
            )
        ]

        case = Case.start(
            job_id="test-job-123",
            name="Test Case",
            account=account,
            description="Test Case Description",
            nodes=case_nodes,
            case_id="test-case-123",
        )

        # Save case to storage
        storage_manager.save_object("Case", case.to_dict)

        # Retrieve case data
        stored_data = storage_manager.get_object_by_id("Case", "test-case-123")
        assert stored_data is not None
        assert stored_data["id"] == "test-case-123"
        assert stored_data["job_id"] == "test-job-123"
        assert stored_data["name"] == "Test Case"

    @patch("supervaizer.account_service.send_event")
    def test_foreign_key_relationships(
        self, mock_send_event, storage_manager, job_context
    ):
        """Test foreign key relationships between Job and Case."""
        # Clear registries
        Jobs().__init__()
        Cases().__init__()

        # Mock the send_event service to avoid HTTP calls
        mock_send_event.return_value = MagicMock()

        # Create job
        job = Job(
            id="test-job-123",
            name="Test Job",
            agent_name="test-agent",
            status=EntityStatus.STOPPED,
            job_context=job_context,
        )

        # Create a proper Account instance for testing
        from supervaizer.account import Account

        account = Account(
            workspace_id="test-workspace",
            api_key="test-api-key",
            api_url="https://test.api.url",
        )

        case_nodes = [
            CaseNode(
                name="Test Node", description="Test Description", type=CaseNoteType.INFO
            )
        ]

        # Create case (should automatically add to job's case_ids)
        case = Case.start(
            job_id="test-job-123",
            name="Test Case",
            account=account,
            description="Test Case Description",
            nodes=case_nodes,
            case_id="test-case-123",
        )

        # Verify foreign key relationship
        assert "test-case-123" in job.case_ids
        assert case.job_id == "test-job-123"

        # Test storage helper method
        storage_manager.save_object("Job", job.to_dict)
        storage_manager.save_object("Case", case.to_dict)

        job_cases = storage_manager.get_cases_for_job("test-job-123")
        assert len(job_cases) == 1
        assert job_cases[0]["id"] == "test-case-123"
