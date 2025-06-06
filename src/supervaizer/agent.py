# Copyright (c) 2024-2025 Alain Prasquier - Supervaize.com. All rights reserved.
#
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
# If a copy of the MPL was not distributed with this file, you can obtain one at
# https://mozilla.org/MPL/2.0/.


import json
import re
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Dict,
    List,
    Optional,
    TypeVar,
)

import shortuuid
from pydantic import BaseModel, field_validator
from rich import inspect, print
from slugify import slugify

from supervaizer.__version__ import VERSION
from supervaizer.common import ApiSuccess, SvBaseModel, log
from supervaizer.event import JobStartConfirmationEvent
from supervaizer.job import Job, JobContext, JobResponse
from supervaizer.job_service import service_job_finished
from supervaizer.lifecycle import EntityStatus
from supervaizer.parameter import ParametersSetup

if TYPE_CHECKING:
    from supervaizer.server import Server

insp = inspect
prnt = print

T = TypeVar("T")


class AgentJobContextBase(BaseModel):
    """
    Base model for agent job context parameters
    """

    job_context: JobContext
    job_fields: Dict[str, Any]


class AgentMethodModel(BaseModel):
    """
    Represents a method that can be called on an agent.

    Attributes:
        name: Display name of the method
        method: Name of the actual method in the project's codebase that will be called with the provided parameters
        params: see below
        fields: see below
        description: Optional description of what the method does


    1. params : Dictionary format
       A simple key-value dictionary of parameters what will be passed to the
       AgentMethod.method as kwargs.
       Example:
       {
           "verbose": True,
           "timeout": 60,
           "max_retries": 3
       }

    2. fields : Form fields format
       These are the values that will be requested from the user in the Supervaize UI
       and also passed as kwargs to the AgentMethod.method.
       A list of field specifications for generating forms/UI, following the
       django.forms.fields definition
       see : https://docs.djangoproject.com/en/5.1/ref/forms/fields/
       Each field is a dictionary with properties like:
       - name: Field identifier
       - type: Python type of the field for pydantic validation
       - field_type: Field type (one of: CharField, IntegerField, BooleanField, ChoiceField, MultipleChoiceField)
       - choices: For choice fields, list of [value, label] pairs
       - default: (optional) Default value for the field
       - widget: UI widget to use (e.g. RadioSelect, TextInput)
       - required: Whether field is required


       Example:
       [
           {
                "name": "color",
                "type": list[str],
                "field_type": "MultipleChoiceField",
                "choices": [["B", "Blue"], ["R", "Red"], ["G", "Green"]],
                "widget": "RadioSelect",
                "required": True,
            },
            {
                "name": "age",
                "type": int,
                "field_type": "IntegerField",
                "widget": "NumberInput",
                "required": False,
            },
       ]

    """

    name: str
    method: str
    params: Dict[str, Any] | None = None
    fields: List[Dict[str, Any]] | None = None
    description: str | None = None
    is_async: bool = False


class AgentMethod(AgentMethodModel):
    @property
    def fields_definitions(self) -> list[Dict[str, Any]]:
        """
        Returns a list of the fields without the type key.
        Used for the API response.
        """
        if self.fields:
            return [
                {k: v for k, v in field.items() if k != "type"} for field in self.fields
            ]
        return []

    @property
    def fields_annotations(self) -> type[BaseModel]:
        """
        Creates and returns a dynamic Pydantic model class based on the field definitions.
        """
        if not self.fields:
            return type("EmptyFieldsModel", (BaseModel,), {"to_dict": lambda self: {}})

        field_annotations = {}
        field_defaults: Dict[str, None] = {}
        for field in self.fields:
            field_name = field["name"]
            field_type = field["type"]
            is_required = field.get("required", False)
            field_annotations[field_name] = (
                field_type if is_required else Optional[field_type]
            )
            if not is_required:
                field_defaults[field_name] = None

        def to_dict(self: BaseModel) -> Dict[str, Any]:
            return {
                field_name: getattr(self, field_name)
                for field_name in self.__annotations__
            }

        return type(
            "DynamicFieldsModel",
            (BaseModel,),
            {
                "__annotations__": field_annotations,
                "to_dict": to_dict,
                **field_defaults,
            },
        )

    @property
    def job_model(self) -> type[AgentJobContextBase]:
        """
        Creates and returns a dynamic Pydantic model class combining job context and job fields.
        """
        fields_model = self.fields_annotations

        return type(
            "AgentAbstractJob",
            (AgentJobContextBase,),
            {
                "__annotations__": {
                    "job_context": JobContext,
                    "job_fields": fields_model,
                    "encrypted_agent_parameters": str | None,
                }
            },
        )

    @property
    def registration_info(self) -> Dict[str, Any]:
        """
        Returns a JSON-serializable dictionary representation of the AgentMethod.
        """
        return {
            "name": self.name,
            "method": str(self.method),
            "params": self.params,
            "fields": self.fields_definitions,
            "description": self.description,
        }


class AgentMethodParams(BaseModel):
    """
    Method parameters for agent operations.

    """

    params: Dict[str, Any] = {"": ""}


class AgentCustomMethodParams(AgentMethodParams):
    method_name: str


class AgentMethodsModel(BaseModel):
    job_start: AgentMethod
    job_stop: AgentMethod
    job_status: AgentMethod
    chat: AgentMethod | None = None
    custom: dict[str, AgentMethod] | None = None

    @field_validator("custom")
    @classmethod
    def validate_custom_method_keys(cls, value):
        """Validate that custom method keys are valid slug-like values suitable for endpoints."""
        if value:
            for key in value.keys():
                # Check if key is a valid slug format
                if not re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", key):
                    raise ValueError(
                        f"Custom method key '{key}' is not a valid slug. "
                        f"Keys must contain only lowercase letters, numbers, and hyphens, "
                        f"and cannot start or end with a hyphen. "
                        f"Examples: 'backup', 'health-check', 'sync-data'"
                    )

                # Additional checks for endpoint safety
                if len(key) > 50:
                    raise ValueError(
                        f"Custom method key '{key}' is too long (max 50 characters)"
                    )

        return value


class AgentMethods(AgentMethodsModel):
    @property
    def registration_info(self) -> Dict[str, Any]:
        return {
            "job_start": self.job_start.registration_info,
            "job_stop": self.job_stop.registration_info,
            "job_status": self.job_status.registration_info,
            "chat": self.chat.registration_info if self.chat else None,
            "custom": {
                name: method.registration_info
                for name, method in (self.custom or {}).items()
            },
        }


class AbstractAgent(SvBaseModel):
    """
    Agent model for the Supervaize Control API.

    """

    supervaizer_VERSION: ClassVar[str] = VERSION
    name: str
    id: str
    author: Optional[str] = None
    developer: Optional[str] = None
    maintainer: Optional[str] = None
    editor: Optional[str] = None
    version: str
    description: str
    tags: list[str] | None = None
    methods: AgentMethods
    parameters_setup: ParametersSetup | None = None
    server_agent_id: str | None = None
    server_agent_status: str | None = None
    server_agent_onboarding_status: str | None = None
    server_encrypted_parameters: str | None = None
    max_execution_time: int


class Agent(AbstractAgent):
    def __init__(
        self,
        name: str,
        id: str | None = None,
        author: Optional[str] = None,
        developer: Optional[str] = None,
        maintainer: Optional[str] = None,
        editor: Optional[str] = None,
        version: str = "",
        description: str = "",
        tags: list[str] | None = None,
        methods: AgentMethods | None = None,
        parameters_setup: ParametersSetup | None = None,
        server_agent_id: str | None = None,
        server_agent_status: str | None = None,
        server_agent_onboarding_status: str | None = None,
        server_encrypted_parameters: str | None = None,
        max_execution_time: int = 60 * 60,  # 1 hour (in seconds)
        **kwargs: Any,
    ) -> None:
        """
        This represents an agent that can be registered with the Supervaize Control API.
        It contains metadata about the agent like name, version, description etc. as well as
        the methods it supports and any parameter configurations.

        The agent ID is automatically generated from the name and must match.

        Attributes:
            name (str): Display name of the agent
            id (str): Unique ID generated from name
            author (str, optional): Original author
            developer (str, optional): Current developer
            maintainer (str, optional): Current maintainer
            editor (str, optional): Current editor
            version (str): Version string
            description (str): Description of what the agent does
            tags (list[str], optional): Tags for categorizing the agent
            methods (AgentMethods): Methods supported by this agent
            parameters_setup (ParametersSetup, optional): Parameter configuration
            server_agent_id (str, optional): ID assigned by server
            server_agent_status (str, optional): Current status on server
            server_agent_onboarding_status (str, optional): Onboarding status
            server_encrypted_parameters (str, optional): Encrypted parameters from server
            max_execution_time (int):  Maximum execution time in seconds, defaults to 1 hour

        Tested in tests/test_agent.py
        """
        # Validate or generate agent ID
        agent_id = id or shortuuid.uuid(name=name)
        if id is not None and id != shortuuid.uuid(name=name):
            raise ValueError("Agent ID does not match")

        # Initialize using Pydantic's mechanism
        super().__init__(
            name=name,
            id=agent_id,
            author=author,
            developer=developer,
            maintainer=maintainer,
            editor=editor,
            version=version,
            description=description,
            tags=tags,
            methods=methods,
            parameters_setup=parameters_setup,
            server_agent_id=server_agent_id,
            server_agent_status=server_agent_status,
            server_agent_onboarding_status=server_agent_onboarding_status,
            server_encrypted_parameters=server_encrypted_parameters,
            max_execution_time=max_execution_time,
            **kwargs,
        )

    def __str__(self) -> str:
        return f"{self.name} ({self.id})"

    @property
    def slug(self) -> str:
        return slugify(self.name)

    @property
    def path(self) -> str:
        return f"/agents/{self.slug}"

    @property
    def registration_info(self) -> Dict[str, Any]:
        """Returns registration info for the agent"""
        return {
            "name": self.name,
            "id": self.id,
            "author": self.author,
            "developer": self.developer,
            "maintainer": self.maintainer,
            "editor": self.editor,
            "version": self.version,
            "description": self.description,
            "api_path": self.path,
            "slug": self.slug,
            "tags": self.tags,
            "methods": self.methods.registration_info,
            "parameters_setup": self.parameters_setup.registration_info
            if self.parameters_setup
            else None,
            "server_agent_id": self.server_agent_id,
            "server_agent_status": self.server_agent_status,
            "server_agent_onboarding_status": self.server_agent_onboarding_status,
            "server_encrypted_parameters": self.server_encrypted_parameters,
            "max_execution_time": self.max_execution_time,
        }

    def update_agent_from_server(self, server: "Server") -> Optional["Agent"]:
        """
        Update agent attributes and parameters from server registration information.
        Example of agent_registration data is available in mock_api_responses.py

        Server is used to decrypt parameters if needed
        Tested in tests/test_agent.py/test_agent_update_agent_from_server
        """
        if server.supervisor_account:
            if self.server_agent_id:
                # Get agent by ID from SaaS Server
                from_server = server.supervisor_account.get_agent_by(
                    agent_id=self.server_agent_id
                )

            else:
                # Get agent by name from SaaS Server
                from_server = server.supervisor_account.get_agent_by(
                    agent_slug=self.slug
                )
        else:
            return None
        if not isinstance(from_server, ApiSuccess):
            log.error(f"[Agent update_agent_from_server] Failed : {from_server}")
            return None

        agent_from_server = from_server.detail
        server_agent_id = agent_from_server.get("id") if agent_from_server else None

        # This should never happen, but just in case
        if self.server_agent_id and self.server_agent_id != server_agent_id:
            message = f"Agent ID mismatch: {self.server_agent_id} != {server_agent_id}"
            raise ValueError(message)

        # Update agent attributes
        self.server_agent_id = server_agent_id
        self.server_agent_status = (
            agent_from_server.get("status") if agent_from_server else None
        )
        self.server_agent_onboarding_status = (
            agent_from_server.get("onboarding_status") if agent_from_server else None
        )

        # If agent is configured, get encrypted parameters
        if self.server_agent_onboarding_status == "configured":
            log.debug(
                f"[Agent configured] getting encrypted parameters for {self.name}"
            )
            server_encrypted_parameters = (
                agent_from_server.get("parameters_encrypted")
                if agent_from_server
                else None
            )
            self.update_parameters_from_server(server, server_encrypted_parameters)
        else:
            log.debug("[Agent not onboarded] skipping encrypted parameters")

        return self

    def update_parameters_from_server(
        self, server: "Server", server_encrypted_parameters: str
    ) -> None:
        if server_encrypted_parameters and self.parameters_setup:
            self.server_encrypted_parameters = server_encrypted_parameters
            decrypted = server.decrypt(server_encrypted_parameters)
            self.parameters_setup.update_values_from_server(json.loads(decrypted))
        else:
            log.debug("[No encrypted parameters] for {self.name}")

    def _execute(self, action: str, params: Dict[str, Any] = {}) -> JobResponse:
        """
        Execute an agent method and return a JobResponse
        """
        module_name, func_name = action.rsplit(".", 1)
        module = __import__(module_name, fromlist=[func_name])
        method = getattr(module, func_name)
        log.debug(f"[Agent method] {method.__name__} with params {params}")
        result = method(**params)
        if not isinstance(result, JobResponse):
            raise TypeError(
                f"Method {func_name} must return a JobResponse object, got {type(result).__name__}"
            )
        return result

    def job_start(
        self,
        job: Job,
        job_fields: Dict[str, Any],
        context: JobContext,
        server: "Server",
        method_name: str = "job_start",
    ) -> Job:
        """Execute the agent's start method in the background

        Args:
            job (Job): The job instance to execute
            job_fields (dict): The job-specific parameters
            context (SupervaizeContextModel): The context of the job
        Returns:
            Job: The updated job instance
        """
        log.debug(
            f"[Agent job_start] Run <{self.methods.job_start.method}> - Job <{job.id}>"
        )
        event = JobStartConfirmationEvent(
            job=job,
            account=server.supervisor_account,
        )
        if server.supervisor_account is not None:
            server.supervisor_account.send_event(sender=job, event=event)
        else:
            log.warning(
                f"[Agent job_start] No supervisor account defined for server, skipping event send for job {job.id}"
            )

        # Mark job as in progress when execution starts
        job.add_response(
            JobResponse(
                job_id=job.id,
                status=EntityStatus.IN_PROGRESS,
                message="Starting job execution",
                payload=None,
            )
        )

        # Execute the method
        if method_name == "job_start":
            action = self.methods.job_start
        else:
            action = self.methods.custom[method_name]

        action_method = action.method
        method_params = action.params or {}
        params = (
            method_params
            | {"fields": job_fields}
            | {"context": context}
            | {"agent_parameters": job.agent_parameters}
        )
        log.debug(
            f"[Agent job_start] action_method : {action_method} - params : {params}"
        )
        try:
            if self.methods.job_start.is_async:
                # TODO: Implement async job execution & test
                raise NotImplementedError(
                    "[Agent job_start] Async job execution is not implemented"
                )
                started = self._execute(action_method, params)
                job_response = JobResponse(
                    job_id=job.id,
                    status=EntityStatus.IN_PROGRESS,
                    message="Job started ",
                    payload={"intermediary_deliverable": started},
                )
            else:
                job_response = self._execute(action_method, params)
                if (
                    job_response.status == EntityStatus.COMPLETED
                    or job_response.status == EntityStatus.FAILED
                    or job_response.status == EntityStatus.CANCELLED
                    or job_response.status == EntityStatus.CANCELLING
                ):
                    job.add_response(job_response)
                    service_job_finished(job, server=server)
                elif job_response.status == EntityStatus.AWAITING:
                    log.debug(
                        f"[Agent job_start] Job is awaiting input, adding response : Job {job.id} status {job_response} §SAS02"
                    )
                    job.add_response(job_response)
                else:
                    log.warning(
                        f"[Agent job_start] Job is not a terminal status, skipping job finish : Job {job.id} status {job_response} §SAS01"
                    )

        except Exception as e:
            # Handle any execution errors
            error_msg = f"Job execution failed: {str(e)}"
            log.error(f"[Agent job_start] Job failed : {job.id} - {error_msg}")
            job_response = JobResponse(
                job_id=job.id,
                status=EntityStatus.FAILED,
                message=error_msg,
                payload=None,
                error=e,
            )
            job.add_response(job_response)
            raise
        return job

    def job_stop(self, params: Dict[str, Any] = {}) -> Any:
        method = self.methods.job_stop.method
        return self._execute(method, params)

    def job_status(self, params: Dict[str, Any] = {}) -> Any:
        method = self.methods.job_status.method
        return self._execute(method, params)

    def chat(self, context: str, message: str) -> Any:
        if not self.methods.chat:
            raise ValueError("Chat method not configured")
        method = self.methods.chat.method
        params = {"context": context, "message": message}
        return self._execute(method, params)

    @property
    def custom_methods_names(self) -> list[str] | None:
        if self.methods.custom:
            return list(self.methods.custom.keys())
        return None


class AgentResponse(BaseModel):
    """Response model for agent endpoints - values provided by Agent.registration_info"""

    name: str
    id: str
    author: Optional[str] = None
    developer: Optional[str] = None
    maintainer: Optional[str] = None
    editor: Optional[str] = None
    version: str
    api_path: str
    description: str
    tags: Optional[list[str]] = None
    methods: Optional[AgentMethods] = None
    parameters_setup: Optional[List[Dict[str, Any]]] = None
    server_agent_id: Optional[str] = None
    server_agent_status: Optional[str] = None
    server_agent_onboarding_status: Optional[str] = None
    server_encrypted_parameters: Optional[str] = None
