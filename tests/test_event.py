# Copyright (c) 2024-2025 Alain Prasquier - Supervaize.com. All rights reserved.
#
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
# If a copy of the MPL was not distributed with this file, you can obtain one at
# https://mozilla.org/MPL/2.0/.


from supervaizer import (
    Account,
    Agent,
    AgentRegisterEvent,
    Case,
    CaseNodeUpdate,
    CaseStartEvent,
    CaseUpdateEvent,
    Event,
    EventType,
    JobFinishedEvent,
    JobStartConfirmationEvent,
    Server,
    ServerRegisterEvent,
)
from supervaizer.job import Job


def test_event(event_fixture: Event) -> None:
    assert isinstance(event_fixture, Event)
    assert event_fixture.type == EventType.AGENT_WAKEUP
    assert event_fixture.source == {"test": "value"}
    assert event_fixture.details == {"test": "value"}
    assert (
        list(event_fixture.payload.keys()).sort()
        == [
            "name",
            "source",
            "workspace_id",
            "event_type",
            "details",
        ].sort()
    )


def test_agent_register_event(agent_fixture: Agent, account_fixture: Account) -> None:
    agent_register_event = AgentRegisterEvent(
        agent=agent_fixture,
        account=account_fixture,
        polling=False,
    )
    assert isinstance(agent_register_event, AgentRegisterEvent)
    assert agent_register_event.type == EventType.AGENT_REGISTER
    assert agent_register_event.source == {"agent": agent_fixture.slug}
    assert agent_register_event.details["name"] == "agentName"
    assert agent_register_event.details["polling"] is False


def test_server_register_event(
    server_fixture: Server, account_fixture: Account
) -> None:
    server_register_event = ServerRegisterEvent(
        server=server_fixture,
        account=account_fixture,
    )
    assert isinstance(server_register_event, ServerRegisterEvent)
    assert server_register_event.type == EventType.SERVER_REGISTER
    assert server_register_event.source == {"server": server_fixture.uri}
    assert server_register_event.details == server_fixture.registration_info


def test_case_start_event(case_fixture: Case, account_fixture: Account) -> None:
    case_start_event = CaseStartEvent(
        case=case_fixture,
        account=account_fixture,
    )
    assert isinstance(case_start_event, CaseStartEvent)
    assert case_start_event.type == EventType.CASE_START
    assert case_start_event.source == {
        "job": case_fixture.job_id,
        "case": case_fixture.id,
    }
    assert case_start_event.details == case_fixture.registration_info


def test_case_update_event(
    case_fixture: Case,
    account_fixture: Account,
    case_node_update_fixture: CaseNodeUpdate,
) -> None:
    case_update_event = CaseUpdateEvent(
        case=case_fixture,
        account=account_fixture,
        update=case_node_update_fixture,
    )
    assert isinstance(case_update_event, CaseUpdateEvent)
    assert case_update_event.type == EventType.CASE_UPDATE
    assert case_update_event.source == {
        "job": case_fixture.job_id,
        "case": case_fixture.id,
    }
    assert case_update_event.details == case_node_update_fixture.registration_info


def test_job_start_confirmation_event(
    job_fixture: Job, account_fixture: Account
) -> None:
    job_start_confirmation_event = JobStartConfirmationEvent(
        job=job_fixture,
        account=account_fixture,
    )
    assert isinstance(job_start_confirmation_event, JobStartConfirmationEvent)
    assert job_start_confirmation_event.type == EventType.JOB_START_CONFIRMATION
    assert job_start_confirmation_event.source == {"job": "test-job-id"}
    assert job_start_confirmation_event.details == job_fixture.registration_info


def test_job_finished_event(job_fixture: Job, account_fixture: Account) -> None:
    job_finished_event = JobFinishedEvent(
        job=job_fixture,
        account=account_fixture,
    )
    assert isinstance(job_finished_event, JobFinishedEvent)
