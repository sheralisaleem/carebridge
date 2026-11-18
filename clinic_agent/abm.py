"""Compatibility entry points for the clinic model."""
from .agents.base import Agent, AgentMessage, PERFORMATIVES
from .simulation.environment import ClinicEnvironment
from .simulation.society import ClinicSociety, build_society, registered_patients
