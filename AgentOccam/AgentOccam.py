from AgentOccam.obs_opt import parse_node_descendants, parse_node_ancestors, parse_node_siblings, action_set_invisible, action_set_visible, action_set_visible_if_with_name, translate_node_to_str, construct_new_DOM_with_visible_nodes
from AgentOccam.llms.claude import call_claude, call_claude_with_messages, arrange_message_for_claude
from AgentOccam.llms.mistral import call_mistral, call_mistral_with_messages, arrange_message_for_mistral
from AgentOccam.llms.cohere import call_cohere, call_cohere_with_messages, arrange_message_for_cohere
from AgentOccam.llms.llama import call_llama, call_llama_with_messages, arrange_message_for_llama
from AgentOccam.llms.titan import call_titan, call_titan_with_messages, arrange_message_for_titan
from AgentOccam.llms.gpt import call_gpt, call_gpt_with_messages, arrange_message_for_gpt
from AgentOccam.llms.gemini import call_gemini, call_gemini_with_messages, arrange_message_for_gemini
from AgentOccam.llms.adk import ADK_AVAILABLE, call_adk, call_adk_with_messages, arrange_message_for_adk
from AgentOccam.action_parser import action_name, normalize_type_action, parse_element_id, parse_type
from AgentOccam.model_registry import (
    ARRANGE_MESSAGE_FOR_MODEL_MAP,
    CALL_MODEL_MAP,
    CALL_MODEL_WITH_MESSAGES_FUNCTION_MAP,
    detect_model_family,
)
from AgentOccam.prompt_loader import build_bulleted_specifications, build_output_specifications
from AgentOccam.utils import CURRENT_DIR, HOMEPAGE_URL
from AgentOccam.logger import logger
from browser_env.processors import TreeNode

from typing import Dict
import re
import copy
import os
from functools import partial
import random
import json

import warnings
warnings.filterwarnings("ignore")


DEFAULT_DOCUMENTED_INTERACTION_ELEMENTS = ["observation", "action"]
DEFAULT_ONLINE_INTERACTION_ELEMENTS = ["url", "observation"]

class Agent:
    def __init__(self, config, objective, prompt_template):
        self.config = config
        self.objective = objective
        self.prompt_template = prompt_template

        if hasattr(self.config, "documented_interaction_elements"):
            self.previous_interactions = {k: [] for k in set(DEFAULT_DOCUMENTED_INTERACTION_ELEMENTS+self.config.documented_interaction_elements)}
        else:
            self.previous_interactions = {k: [] for k in DEFAULT_DOCUMENTED_INTERACTION_ELEMENTS}
        if hasattr(self.config, "online_interaction_elements"):
            self.online_interaction = {k: None for k in set(DEFAULT_ONLINE_INTERACTION_ELEMENTS+self.config.online_interaction_elements)}
        else:
            self.online_interaction = {k: None for k in DEFAULT_ONLINE_INTERACTION_ELEMENTS}

        self.model_family = detect_model_family(self.config.model)
        self.call_model = partial(CALL_MODEL_MAP[self.model_family], model_id=self.config.model)
        self.call_model_with_message = partial(CALL_MODEL_WITH_MESSAGES_FUNCTION_MAP[self.model_family], model_id=self.config.model)
        self.arrange_message_for_model = ARRANGE_MESSAGE_FOR_MODEL_MAP[self.model_family]
        self.runtime_model_id = self.config.model
        self._fallback_from_missing_adk_if_needed()
        
        # ADK orchestration support
        self.adk_tools = []
        self.adk_context = {}
        self.use_adk_orchestration = (
            self.model_family == 'adk' and
            ADK_AVAILABLE and
            hasattr(config, 'adk_config') and 
            getattr(config.adk_config, 'orchestration_mode', None) is not None
        )

    def _fallback_from_missing_adk_if_needed(self):
        if self.model_family != "adk" or ADK_AVAILABLE:
            return

        requested_model_id = getattr(self.config, "model", "")
        if not isinstance(requested_model_id, str) or not requested_model_id.startswith("adk-"):
            return

        fallback_model_id = requested_model_id[len("adk-"):]
        try:
            self.shift_model(fallback_model_id)
            print(
                f"Warning: google-adk is not installed. "
                f"Falling back from '{requested_model_id}' to '{fallback_model_id}'."
            )
        except Exception as exc:
            print(
                f"Warning: google-adk is not installed and fallback model "
                f"'{fallback_model_id}' could not be initialized: {exc}"
            )
    
    def register_adk_tool(self, tool_definition):
        """Register a single ADK tool for this agent."""
        if self.model_family == 'adk':
            self.adk_tools.append(tool_definition)
    
    def register_adk_toolset(self, toolset_name):
        """Register a predefined ADK toolset."""
        if self.model_family == 'adk':
            # Import toolset definitions (to be implemented in browser_env/adk_tools.py)
            try:
                from browser_env.adk_tools import get_toolset
                tools = get_toolset(toolset_name)
                self.adk_tools.extend(tools)
            except ImportError:
                print(f"Warning: ADK toolset '{toolset_name}' not found. Skipping.")
    
    def get_adk_tools(self):
        """Return currently registered ADK tools."""
        return self.adk_tools if self.model_family == 'adk' else []

    def shift_model(self, model_id):
        self.model_family = detect_model_family(model_id)
        self.call_model = partial(CALL_MODEL_MAP[self.model_family], model_id=model_id)
        self.call_model_with_message = partial(CALL_MODEL_WITH_MESSAGES_FUNCTION_MAP[self.model_family], model_id=model_id)
        self.arrange_message_for_model = ARRANGE_MESSAGE_FOR_MODEL_MAP[self.model_family]
        self.runtime_model_id = model_id

    def prune_message_list(self, message_list):
        return self.merge_adjacent_text([m for m in message_list if not (m[0]=="text" and len(m[1])==0)])
    
    def merge_adjacent_text(self, message_list):
        merged_list = []
        current_tuple = None
        
        for tup in message_list:
            if tup[0] == "text":
                if current_tuple:
                    current_tuple = (current_tuple[0], current_tuple[1] + tup[1])
                else:
                    current_tuple = tup
            else:
                if current_tuple:
                    merged_list.append(current_tuple)
                    current_tuple = None
                merged_list.append(tup)
        
        if current_tuple:
            merged_list.append(current_tuple)
        
        return merged_list

    
    def get_step(self):
        return len(self.previous_interactions["action"])

    def update_objective(self, objective):
        self.objective = objective

    def update_online_state(self, **online_states):
        for k in online_states.keys():
            if k in self.online_interaction.keys():
                self.online_interaction[k] = online_states[k]

    def update_history(self, **interaction_dict):
        for k in interaction_dict.keys():
            if k in self.previous_interactions.keys():
                self.previous_interactions[k].append(interaction_dict[k])

    def equal_history_length(self):
        lengths = [len(self.previous_interactions[k]) for k in self.previous_interactions.keys()]
        return (len(set(lengths)) == 1)

    def parse_elements(self, text, key_list):
        element_dict = {}
        for k in key_list:
            # _match = re.search(rf'{k.upper()}:\s*(.*?)\s*(?=\n[A-Z\d\s\W]*: *\n|$)', text, re.DOTALL)
            _match = re.search(rf'{k.upper()}:\s*(.*?)\s*(?=\n[A-Z\s]*:|$)', text, re.DOTALL)
            element_dict[k] = _match.group(1).strip() if _match else ""
        return element_dict

    def get_output_specifications(self):
        return build_output_specifications(self.config.output)

    def parse_stipulated_action_list(self, text: str, action: str, actions: list) -> str:
        pattern = rf'({re.escape(action)}\s*(.*?))(?=\n(?:{"|".join(map(re.escape, actions))})|$)'
        return [match[0].strip() for match in re.findall(pattern, text, re.DOTALL)]

    def parse_str_to_action_list(self, text:str, actions: list):
        remain_text = copy.deepcopy(text)
        action_list = []
        while remain_text:
            find_action = False
            for action in actions:
                if remain_text.startswith(action):
                    match = re.search(rf'({re.escape(action)}\s*(.*?))(?=\n(?:{"|".join(map(re.escape, actions))})|$)', remain_text, re.DOTALL)
                    action_list.append(match[0])
                    remain_text = remain_text[len(match[0]):].strip()
                    find_action = True
            if not find_action:
                break
        return action_list
    
    def get_observation_text(self, idx=None):
        if isinstance(self.online_interaction["observation"], dict):
            if idx:
                return self.previous_interactions["observation"][idx]["text"]
            return self.online_interaction["observation"]["text"]
        elif isinstance(self.online_interaction["observation"], str):
            if idx:
                return self.previous_interactions["observation"][idx]
            return self.online_interaction["observation"]
        
    def get_observation_image(self, idx=None):
        if isinstance(self.online_interaction["observation"], dict):
            if idx:
                return self.previous_interactions["observation"][idx]["image"]
            return self.online_interaction["observation"]["image"]
        elif isinstance(self.online_interaction["observation"], str):
            return None
        
    def get_observation_node(self, idx=None):
        if isinstance(self.online_interaction["observation"], dict):
            if idx != None:
                return self.previous_interactions["observation"][idx]["node"]
            return self.online_interaction["observation"]["node"]
        elif isinstance(self.online_interaction["observation"], str):
            return None
        
    def get_observation_node_str(self, idx=None):
        if isinstance(self.online_interaction["observation"], dict):
            if idx != None:
                return self.previous_interactions["observation"][idx]["node_str"]
            return translate_node_to_str(self.online_interaction["observation"]["node"], mode="name_only")
        elif isinstance(self.online_interaction["observation"], str):
            return None
        
    def del_observation_node(self):
        if isinstance(self.online_interaction["observation"], str):
            return
        if isinstance(self.online_interaction["observation"], dict):
            for idx in range(len(self.previous_interactions["observation"])):
                if "node" in self.previous_interactions["observation"][idx].keys() and self.previous_interactions["observation"][idx]["node"]:
                    node_str = translate_node_to_str(self.previous_interactions["observation"][idx]["node"], mode="name_only")
                    self.previous_interactions["observation"][idx]["node_str"] = node_str
                    self.previous_interactions["observation"][idx]["node"].delete_tree()
                    self.previous_interactions["observation"][idx]["node"] = None

class PlanTreeNode:
    def __init__(self, id, type, text, level, url, step):
        self.visible = True
        self.id = id
        self.type = type
        self.text = text
        self.level = level
        self.url = url
        self.step = step
        self.children = []
        self.parent = None
        self.note = []
        self.hint = []
        self.resume_reason = []
        self.steps_taken = []

    def reset(self):
        self.visible = True
        self.note = []
        self.hint = []
        self.steps_taken = []

    def add_child(self, child):
        child.parent = self
        self.children.append(child)

    def search_node_by_id(self, target_id):
        if self.visible and self.id == target_id:
            return self
        for child in self.children:
            result = child.search_node_by_id(target_id)
            if result:
                return result
        return None
    
    def traverse(self, action=None, tree_buffer=[]):
        res_action = action(self)
        if res_action:
            if isinstance(res_action, list):
                tree_buffer.extend(res_action)
            else:
                tree_buffer.append(res_action)
        for child in self.children:
            child.traverse(action, tree_buffer=tree_buffer)

class QAActor(Agent):
    def __init__(self, config, objective, prompt_template):
        super().__init__(config, objective, prompt_template)
    def get_instruction(self):
        return self.prompt_template["instruction_template"]
    def get_online_input(self):
        return [("text", self.prompt_template["input_template"].replace("{current_observation}", self.get_observation_text()).replace("{objective}", self.objective))]
    def get_action(self, instruction, online_input):
        model_response = self.call_model_with_message(system_prompt=instruction, messages=self.arrange_message_for_model(online_input))
        action_elements = self.parse_elements(text=model_response, key_list=self.config.output)
        action = action_elements["response"]
        action_elements["action"] = f"note [{action}]"
        action_elements["instruction"] = instruction
        action_elements["input"] = online_input
        return model_response, action_elements
    
class PlanningActor(Agent):
    def __init__(self, config, objective, prompt_template):
        super().__init__(config, objective, prompt_template)
        self.instruction = None

    def get_planning_specifications(self):
        return "\n".join(["- " + "".join(open(os.path.join(CURRENT_DIR, "AgentOccam", "prompts", "planning_specifications", f"{p}.txt"), "r").readlines()) for p in self.config.planning_command])
    
    def get_instruction(self):
        if self.instruction:
            return self.instruction
        output_specifications = self.get_output_specifications()
        self.instruction = self.prompt_template["instruction_template"].replace("{output_specifications}", output_specifications).replace("{planning_specifications}", self.get_planning_specifications())
        return self.instruction
    
    def get_online_input(self):
        return None
    
    def get_action(self, instruction, online_input):
        model_response = self.call_model_with_message(system_prompt=instruction, messages=self.arrange_message_for_model(online_input))
        action_elements = self.parse_elements(text=model_response, key_list=self.config.output)
        action_elements["action"] = copy.deepcopy(action_elements["plan"])
        del action_elements["plan"]
        action_elements["reason"] = "N/A"
        action_elements["instruction"] = instruction
        action_elements["input"] = online_input
        return model_response, action_elements

class ReflectionActor(Agent):
    def __init__(self, config, objective, prompt_template):
        super().__init__(config, objective, prompt_template)
        self.instruction = None

    def get_planning_specifications(self):
        return "\n".join(["- " + "".join(open(os.path.join(CURRENT_DIR, "AgentOccam", "prompts", "planning_specifications", f"{p}.txt"), "r").readlines()) for p in self.config.planning_command])
    
    def get_navigation_specifications(self):
        return "\n".join(["- " + "".join(open(os.path.join(CURRENT_DIR, "AgentOccam", "prompts", "navigation_specifications", f"{n}.txt"), "r").readlines()) for n in self.config.navigation_command])
    
    def get_instruction(self):
        if self.instruction:
            return self.instruction
        output_specifications = self.get_output_specifications()
        planning_specifications = self.get_planning_specifications()
        navigation_specifications = self.get_navigation_specifications()
        instruction = self.prompt_template["instruction_template"]
        instruction = instruction.replace("{output_specifications}", output_specifications)
        instruction = instruction.replace("{planning_specifications}", planning_specifications)
        instruction = instruction.replace("{navigation_specifications}", navigation_specifications)
        self.instruction = instruction
        return self.instruction
    
    def get_online_input(self):
        return None
    
    def get_action(self, instruction, online_input):
        model_response = self.call_model_with_message(system_prompt=instruction, messages=self.arrange_message_for_model(online_input))
        action_elements = self.parse_elements(text=model_response, key_list=self.config.output)
        action_elements["instruction"] = instruction
        action_elements["input"] = online_input
        return model_response, action_elements

IDENTITY_CLASS_MAP = {
    "QA": QAActor,
    "planning": PlanningActor,
    "reflection": ReflectionActor,
}

class Actor(Agent):
    def __init__(self, config, objective, prompt_template, plan_tree_node, is_gherkin_task=False, is_isp_task=False):
        super().__init__(config, objective, prompt_template)
        self.plan_tree_root = plan_tree_node
        self.active_node = plan_tree_node
        self.output_specifications = None
        self.planning_specifications = None
        self.navigation_specifications = None
        self.criticism_element_list = None
        self.is_gherkin_task = is_gherkin_task
        self.is_isp_task = is_isp_task

        self.output_play_path = os.path.join(CURRENT_DIR, f"play-{self.config.others.logname}.txt") if getattr(self.config.others, "logname", "") != "" else os.path.join(CURRENT_DIR, f"play.txt")
        self.output_trash_path = os.path.join(CURRENT_DIR, f"trash-{self.config.others.logname}.txt") if getattr(self.config.others, "logname", "") != "" else os.path.join(CURRENT_DIR, f"trash.txt")

        self.identities = []
        if hasattr(self.config, "identities"):
            i = 0
            while hasattr(self.config.identities, f"identity_{i}"):
                identity_config = getattr(self.config.identities, f"identity_{i}")
                self.identities.append(IDENTITY_CLASS_MAP[identity_config.name](identity_config, objective=objective, prompt_template=prompt_template[identity_config.name]))
                i += 1

    def _build_retry_exhausted_action(self, instruction, online_input, attempts):
        failure_reason = f"Failed to produce a valid action after {attempts} attempts."
        action_elements = {k: "" for k in self.config.output}
        action_elements["reason"] = failure_reason
        action_elements["action"] = f"stop [{failure_reason}]"
        action_elements["instruction"] = instruction
        action_elements["input"] = online_input
        return action_elements
    
    def update_online_state(self, **online_states):
        super().update_online_state(**online_states)
        for identity in self.identities:
            identity.update_online_state(**online_states)

    def is_planning(self, action):
        for c in self.config.planning_command:
            if action.startswith(c):
                return c
        return False

    def is_navigation(self, action):
        action_without_note = re.sub(rf'(note\s*(.*?))(?=\n(?:{"|".join(map(re.escape, self.config.navigation_command))})|$)', "", action).strip()
        for c in self.config.navigation_command:
            if action_without_note.startswith(c):
                return c
        return False
    
    def is_valid_action(self, action_str):
        action = action_name(action_str)
        match action:
            case "click":
                element_id = parse_element_id(action_str)
                if element_id is None:
                    return False
                if str(element_id) in self.get_observation_text():
                    return True
                return False
            case "hover":
                element_id = parse_element_id(action_str)
                if element_id is None:
                    return False
                if str(element_id) in self.get_observation_text():
                    return True
                return False
            case "type":
                parsed = parse_type(action_str)
                if not parsed:
                    return False
                element_id, text, enter_flag = parsed
                if enter_flag:
                    text += "\n"
                if str(element_id) in self.get_observation_text():
                    return True
            case "go_back":
                return True
            case "go_home":
                return True
            case "note":
                return True
            case "stop":
                return True
            case "branch":
                return True
            case "prune":
                return True
            case "goto":
                return True
            case "scroll":
                return True

    def are_valid_actions(self, actions):
        action_list = self.parse_str_to_action_list(actions, self.config.planning_command+self.config.navigation_command+["goto"])
        if not action_list:
            return False
        for action in action_list:
            if not self.is_valid_action(action):
                return False
        return True

    def get_previous_plans(self, verbose=False):
        def action_return_visible_node(node, verbose=False):
            if node.id == self.active_node.id:
                basic = "\t" * node.level + f"[{node.id}] (Active Plan) {node.text}" if node.visible else None
            else:
                basic = "\t" * node.level + f"[{node.id}] {node.text}" if node.visible else None
            if basic and len(node.resume_reason) > 0:
                basic += f" # Was resumed to this step {len(node.resume_reason)} time(s) for:"
                for i, reason in enumerate(node.resume_reason):
                    basic += f" {i}. {reason}"
            if verbose and basic and len(node.note) > 0:
                for i, note in enumerate(node.note):
                    basic += "\n" + "\t" * node.level + f"Note {i}. {note}"
            return basic
        plan_tree_buffer = []
        parse_node_descendants(self.plan_tree_root, partial(action_return_visible_node, verbose=verbose), tree_buffer=plan_tree_buffer)
        return "\n".join(plan_tree_buffer)
    
    def get_active_plan(self):
        return f"[{self.active_node.id}] {self.active_node.text}"
    
    def get_interaction_history(self, interaction_history_config=False, mode="highlight"):
        interaction_history_config = interaction_history_config if interaction_history_config else self.config.interaction_history

        previous_observation = []
        for i in self.active_node.steps_taken:
            if self.get_observation_node_str() and self.get_observation_node_str(i) and not self.get_observation_node_str() == self.get_observation_node_str(i):
                if self.previous_interactions["observation highlight"][i] and mode == "highlight" and len(translate_node_to_str(self.previous_interactions["observation highlight"][i], mode="name_only", retained_ids=self.previous_interactions["retained element ids"][i]).split()) < 200:
                    try:
                        previous_observation.append({"text": translate_node_to_str(self.previous_interactions["observation highlight"][i], mode="name_only", retained_ids=self.previous_interactions["retained element ids"][i]), "image": self.get_observation_image(i)})
                    except:
                        print(i, self.previous_interactions["observation"][i]["text"])
                        raise ValueError("Cannot translate highlight node to text.")
                else:
                    previous_observation.append({"text": self.previous_interactions["observation summary"][i], "image": self.get_observation_image(i)})
            elif not self.get_observation_node() or mode == "full":
                if len(self.get_observation_text(i).split()) < 200:
                    previous_observation.append({"text": self.get_observation_text(i), "image": self.get_observation_image(i)})
                else:
                    previous_observation.append({"text": self.previous_interactions["observation summary"][i], "image": self.get_observation_image(i)})
            else:
                previous_observation.append({"text": "The same as the CURRENT OBSERVATION (see below CURRENT OBSERVATION section).", "image": self.get_observation_image(i)})

        previous_observation_summary = [self.previous_interactions["observation summary"][i] for i in self.active_node.steps_taken]

        def get_text(obs):
            if isinstance(obs, dict):
                return obs["text"]
            elif isinstance(obs, str):
                return obs

        def get_image(obs):
            if isinstance(obs, dict):
                return obs["image"]
            elif isinstance(obs, str):
                return obs

        if interaction_history_config.step_num == "all":
            textual_observations = [get_text(obs) for obs in previous_observation] if interaction_history_config.verbose else previous_observation_summary
            visual_observations = [get_image(obs) for obs in previous_observation]
        else:
            textual_observations = previous_observation_summary[:-interaction_history_config.step_num]
            visual_observations = [None] * len(previous_observation_summary[:-interaction_history_config.step_num])
            textual_observations += [get_text(obs) for obs in previous_observation][-interaction_history_config.step_num:] if interaction_history_config.verbose else previous_observation_summary[-interaction_history_config.step_num:]
            visual_observations += [get_image(obs) for obs in previous_observation][-interaction_history_config.step_num:]

        plans = [self.previous_interactions["plan"][i] for i in self.active_node.steps_taken]
        reasons = [self.previous_interactions["reason"][i] for i in self.active_node.steps_taken]
        actions = [self.previous_interactions["action"][i] for i in self.active_node.steps_taken]
            
        if "image" in interaction_history_config.type:
            message_list = []
            for step, (obs, vi_obs, plan, reason, action) in enumerate(zip(textual_observations, visual_observations, plans, reasons, actions)):
                message_list.append(("text", f"<step_{step}_interaction>\n"))
                if vi_obs:
                    message_list.append(("text", "VISUAL OBSERVATION:\n"))
                    message_list.append(("image", vi_obs))
                if self.active_node.id != 0:
                    message_list.append(("text", f"TEXTUAL OBSERVATION:\n{obs}\nACTIVE PLAN:\n{plan}\nREASON FOR ACTION:\n{reason}\nACTION:\n{action}\n</step_{step}_interaction>\n"))
                else:
                    message_list.append(("text", f"TEXTUAL OBSERVATION:\n{obs}\nREASON FOR ACTION:\n{reason}\nACTION:\n{action}\n</step_{step}_interaction>\n"))
            return self.prune_message_list(message_list=message_list)
        else:
            message = ""
            for step, (obs, plan, reason, action) in enumerate(zip(textual_observations, plans, reasons, actions)):
                if self.active_node.id != 0:
                    message += f"<step_{step}_interaction>\nOBSERVATION:\n{obs}\nACTIVE PLAN:\n{plan}\nREASON FOR ACTION:\n{reason}\nACTION:\n{action}\n</step_{step}_interaction>\n" # f"<step_{step}_interaction>\nOBSERVATION:\n{obs}\nACTIVE PLAN:\n{plan}\nREASON FOR ACTION:\n{reason}\nACTION:\n{action}\n</step_{step}_interaction>\n"
                else:
                    message += f"<step_{step}_interaction>\nOBSERVATION:\n{obs}\nREASON FOR ACTION:\n{reason}\nACTION:\n{action}\n</step_{step}_interaction>\n" # f"<step_{step}_interaction>\nOBSERVATION:\n{obs}\nREASON FOR ACTION:\n{reason}\nACTION:\n{action}\n</step_{step}_interaction>\n"
            return self.prune_message_list(message_list=[("text", message)])
        
    def pre_process_atomic_actions(self, atomic_action_list=["combobox"]):
        if self.get_observation_node() and "combobox" in atomic_action_list:
            self.online_interaction["observation"]["text"] = translate_node_to_str(self.get_observation_node(), mode="concise", hidden_roles=["menu", "combobox", "listbox"])

    def get_online_input(self, criticism_elements):
        input_template = self.prompt_template["input_template"]
        input_prefix, input_suffix = input_template.split("{input}")
        INPUT_TYPE_TO_CONTENT_MAP = {
            "step": self.get_step(),
            "objective": self.objective,
            "previous plans": self.get_previous_plans(verbose=True),
            "interaction history": self.get_interaction_history(),
            "current observation": self.get_observation_text(),
            "current visual observation": self.get_observation_image()
        }
        input_list = []
        for input_type in self.config.input:
            input_content = None
            if input_type == "current visual observation":
                continue
            elif input_type in INPUT_TYPE_TO_CONTENT_MAP.keys():
                input_content = INPUT_TYPE_TO_CONTENT_MAP[input_type]
            elif input_type.startswith("critic: ") and criticism_elements and input_type[len("critic: "):] in criticism_elements.keys() and criticism_elements[input_type[len("critic: "):]]:
                input_type = input_type[len("critic: "):]
                input_content = criticism_elements[input_type]
                input_type = "FROM USER: " + input_type
            if input_content and isinstance(input_content, str):
                input_list.append(("text", f"{input_type.upper()}:\n{input_content}\n"))
            elif input_content and isinstance(input_content, list):
                input_list.append(("text", f"{input_type.upper()}:\n"))
                input_list += input_content if len(input_content) > 0 else ["N/A"]

        if "image" in self.config.current_observation.type:
            input_type = "current visual observation"
            input_list.append(("text", f"{input_type.upper()}:\n"))
            input_list.append(("image", INPUT_TYPE_TO_CONTENT_MAP["current visual observation"]))

        return self.prune_message_list(message_list=[("text", input_prefix)] + input_list + [("text", input_suffix)])
    
    def get_planning_specifications(self):
        if self.planning_specifications:
            return self.planning_specifications
        self.planning_specifications = build_bulleted_specifications("planning_specifications", self.config.planning_command)
        return self.planning_specifications
    
    def get_navigation_specifications(self):
        if self.navigation_specifications:
            return self.navigation_specifications
        
        spec_names = []
        for n in self.config.navigation_command:
            # Priority: ISP task → stop_isp.txt > Gherkin task → stop_gherkin.txt > default
            if n == "stop" and getattr(self, "is_isp_task", False):
                spec_name = "stop_isp"
            elif n == "stop" and self.is_gherkin_task:
                spec_name = "stop_gherkin"
            else:
                spec_name = n
            spec_names.append(spec_name)

        self.navigation_specifications = build_bulleted_specifications("navigation_specifications", spec_names)
        return self.navigation_specifications
    
    def get_actor_instruction(self, examples=None):
        if self.config.planning_command:
            instruction = self.prompt_template["instruction_template"]["with_planning"]
        else:
            instruction = self.prompt_template["instruction_template"]["without_planning"]
        output_specifications = self.get_output_specifications()
        planning_specifications = self.get_planning_specifications()
        navigation_specifications = self.get_navigation_specifications()
        instruction = instruction.replace("{output_specifications}", output_specifications)
        instruction = instruction.replace("{planning_specifications}", planning_specifications)
        instruction = instruction.replace("{navigation_specifications}", navigation_specifications)

        example_source = examples if examples is not None else self.prompt_template.get("examples", [])
        if len(example_source) > 0:
            instruction += f"\n\n## Here are a few examples:"
            for i, example in enumerate(example_source):
                example_input = example["input"]
                example_output = example["output"]
                if "example_template" in self.prompt_template.keys():
                    instruction += "\n\n"
                    instruction += self.prompt_template.get("example_template", "| Example {i}\n### Input:\n{example_input}\n### Response: Let's think step by step.\n{example_response}").replace("{i}", i).replace("{example_input}", example_input).replace("{example_output}", example_output)
                else:
                    instruction += f"\n\n| Example {i}\n\n### Input:\n{example_input}\n\n### Response: Let's think step by step.\n{example_output}"
        
        if self.get_step() == self.config.others.max_steps - 1:
            instruction += f"\n\nWARNING: You have a {self.config.others.max_steps}-step budget, and this would be your FINAL STEP. Wrap up your observations and return your answer with `stop [answer]` to maximize the reward."
        # else:
        #     instruction += f"\n\nWARNING: You have a {self.config.others.max_steps}-step budget, and there are {self.config.others.max_steps-self.get_step()} remaining attempts."

        return instruction
    
    def verbose(self, instruction, online_input, model_response_list, action_element_list):
        action_element_keys = [k for k in self.config.play if k in action_element_list[0].keys()]
        other_play_keys = [k for k in self.config.play if k not in action_element_list[0].keys()]

        VERBOSE_TO_CONTENT_MAP = {
            "step": self.get_step(),
            "objective": self.objective,
            "previous plans": self.get_previous_plans(verbose=True),
            "url": self.online_interaction["url"],
            "observation": self.get_observation_text(),
            "response": "\n~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~\n".join([f"|\tAgent {i}:\n{model_response}" for i, model_response in enumerate(model_response_list[:self.config.number])]) if self.config.number > 1 else model_response_list[0],
            "instruction": instruction,
            "online input": "\n".join([i[1] for i in online_input if i[0]=="text"]),
            "alter ego response": "\n~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~\n".join(["|\tAgent {}:\n{}".format(identity.config.name, response) for identity, response in zip(self.identities, model_response_list[self.config.number:])])
        }

        if self.config.others.verbose > 0 and self.config.verbose > 0:
            with open(self.output_trash_path, "a", encoding="utf-8") as af:
                af.write("-"*32+"ACTOR"+"-"*32+"\n")
            for t in self.config.trash:
                content = VERBOSE_TO_CONTENT_MAP.get(t, "")
                with open(self.output_trash_path, "a", encoding="utf-8") as af:
                    af.write(f"{t.upper()}:\n{content}\n\n")
            with open(self.output_play_path, "w", encoding="utf-8") as _:
                pass
            for p in other_play_keys:
                content = VERBOSE_TO_CONTENT_MAP.get(p, "")
                with open(self.output_play_path, "a", encoding="utf-8") as af:
                    af.write(f"{p.upper()}:\n{content}\n\n")
            for i, action_elements in enumerate(action_element_list):
                if len(action_element_list) > 1:
                    with open(self.output_play_path, "a", encoding="utf-8") as af:
                        af.write("-"*32+f"AGENT {i}"+"-"*32+"\n")
                for action_element_key in action_element_keys:
                    content = action_elements.get(action_element_key, "N/A")
                    with open(self.output_play_path, "a", encoding="utf-8") as af:
                        af.write(f"{action_element_key.upper()}:\n{content}\n\n")
    
    def parse_plan(self, planning):
        planning_type = self.is_planning(action=planning)
        match = re.search(
            rf"{planning_type} ?\[(\d+)\] ?\[(.+)\]", planning, re.DOTALL
        )
        if not match:
            raise ValueError("Invalid planning command.")
        node_id, planning_content = (
            int(match.group(1)),
            match.group(2)
        )
        return planning_type, node_id, planning_content
    
    def prune_planning(self, node:PlanTreeNode, planning_content):
        def set_invisible(node:PlanTreeNode):
            node.visible = False
        def return_steps_taken(node:PlanTreeNode):
            return [node.step] + node.steps_taken
        after_node = False
        if node.id > 0:
            for child in node.parent.children:
                if not after_node and child != node:
                    continue
                elif child == node:
                    after_node = True
                    continue
                child.visible = False
        node.traverse(set_invisible)
        node.reset()
        steps_taken = []
        node.traverse(action=return_steps_taken, tree_buffer=steps_taken)
        node.steps_taken = sorted(list(set(steps_taken)), reverse=False)
        node.resume_reason.append(planning_content)
        navigation = f"goto [{node.url}] [1]"
        self.active_node = node
        return navigation
    
    def branch_planning(self, node, planning_content):
        new_node = PlanTreeNode(id=self.active_node.id+1, type=type, text=planning_content, level=node.level+1, url=self.online_interaction["url"], step=self.get_step())
        self.active_node = new_node
        node.add_child(new_node)
    
    def planning(self, action):
        if action and self.is_planning(action):
            try:
                planning_type, node_id, planning_content = self.parse_plan(planning=action)
                node = self.plan_tree_root.search_node_by_id(node_id)
                if not node:
                    raise ValueError(f"Invalid node id {node_id}: {action}.")
                if planning_type == "prune":
                    navigation_action = self.prune_planning(node=node, planning_content=planning_content)
                    return navigation_action
                elif planning_type == "branch":
                    self.branch_planning(node=node, planning_content=planning_content)
                else:
                    raise ValueError(f"Invalid planning operation {planning_type}: {action}.")
            except Exception as e:
                print("Invalid plan node:", str(e))
                flaw_node = self.active_node
                flaw_node.note.append(f"You previously generate plan \"{action}\", which has INVALID syntax. User planning command like `branch [parent_plan_id] [new_subplan_intent]` or `prune [resume_plan_id] [reason]`.")
        else:
            self.active_node.steps_taken.append(self.get_step())
        return None
    
    def go_home(self, action):
        if "go_home" in action:
            return f"goto [{HOMEPAGE_URL}] [1]"
        return None
    
    def parse_action(self, action_str):
        try:
            DOM_root_node = self.get_observation_node()
            action_str = action_str.strip()
            action = action_name(action_str)
            match action:
                case "click":
                    element_id = parse_element_id(action_str)
                    if element_id is None:
                        raise ValueError(f"Invalid click action {action_str}")
                    node = DOM_root_node.search_node_by_id(element_id)
                    return f"click [{element_id}] ({node.role} {node.name})"
                case "hover":
                    element_id = parse_element_id(action_str)
                    if element_id is None:
                        raise ValueError(f"Invalid hover action {action_str}")
                    node = DOM_root_node.search_node_by_id(element_id)
                    return f"hover [{element_id}] ({node.role} {node.name})"
                case "type":
                    parsed = parse_type(action_str)
                    if not parsed:
                        raise ValueError(f"Invalid type action {action_str}")
                    element_id, text, enter_flag = parsed
                    if enter_flag:
                        text += "\n"
                    node = DOM_root_node.search_node_by_id(element_id)
                    return action + f" ({node.name})"
                case "scroll":
                    return action_str
                case "goto":
                    return action
                case "new_tab":
                    return action
                case "go_back":
                    return action
                case "go_forward":
                    return action
                case "stop":
                    return action

            return False
        except:
            return False
    
    def parse_actions_to_element_ids(self, actions):
        action_str_list = []
        for a in self.config.navigation_command:
            action_str_list += self.parse_stipulated_action_list(text=actions, action=a, actions=self.config.planning_command+self.config.navigation_command+["goto"])
        retained_element_ids = []
        for action_str in action_str_list:
            try:
                action_str = action_str.strip()
                action = action_name(action_str)
                match action:
                    case "click":
                        element_id = parse_element_id(action_str)
                        if element_id is None:
                            raise ValueError(f"Invalid click action {action_str}")
                        retained_element_ids.append(element_id)
                    case "hover":
                        element_id = parse_element_id(action_str)
                        if element_id is None:
                            raise ValueError(f"Invalid hover action {action_str}")
                        retained_element_ids.append(element_id)
                    case "type":
                        action_str = normalize_type_action(action_str)
                        parsed = parse_type(action_str)
                        if not parsed:
                            raise ValueError(f"Invalid type action {action_str}")
                        element_id, text, enter_flag = parsed
                        retained_element_ids.append(element_id)
                    case "scroll":
                        pass
                    case "goto":
                        pass
                    case "new_tab":
                        pass
                    case "go_back":
                        pass
                    case "go_forward":
                        pass
                    case "stop":
                        pass
                    case "note":
                        pass
            except:
                continue

        return retained_element_ids
    
    def take_note(self, action, note_as_action=True):
        if action and "note [" in action:
            none_note_action_list = []
            action_list = self.parse_str_to_action_list(action, actions=self.config.planning_command+self.config.navigation_command+["goto"])
            for a in action_list:
                if "note [" in a:
                    note = re.search(r"note ?\[?(.+)", a, re.DOTALL).group(1)
                    if note.endswith("]"):
                        note = note[:-1]
                    self.active_node.note.append(f"STEP {self.get_step()}: {note}")
                    self.note_buffer = note
                else:
                    none_note_action_list.append(a)
            if note_as_action:
                return action
            return "\n".join(none_note_action_list)
        # action_note = self.parse_action(action)
        # if action_note:
        #     self.active_node.note.append(f"STEP {self.get_step()} ACTION: {action_note}")
        return action
        
    def get_observation_highlight(self, action_elements:dict):
        action_elements["observation highlight idxs"] = copy.deepcopy(action_elements.get("observation highlight", ""))
        DOM_root_node = self.get_observation_node()
        if not DOM_root_node:
            action_elements["observation highlight"] = None
            return
        observation_highlight_idxs = [int(idx.strip()) for idx in action_elements.get("observation highlight", "").split(",") if idx.strip().isdigit()]
        if observation_highlight_idxs:
            parse_node_descendants(node=DOM_root_node, action=action_set_invisible)
            for idx in observation_highlight_idxs:
                try:
                    node = DOM_root_node.search_node_by_id(idx)
                    parse_node_descendants(node=node, action=action_set_visible)
                    parse_node_ancestors(node=node, action=action_set_visible)
                    parse_node_siblings(node=node, action=action_set_visible_if_with_name)
                except:
                    pass
        try: 
            assert DOM_root_node.get_visible_node_number() < 30 and construct_new_DOM_with_visible_nodes(DOM_root=DOM_root_node)
            action_elements["observation highlight"] = construct_new_DOM_with_visible_nodes(DOM_root=DOM_root_node)
            parse_node_descendants(node=DOM_root_node, action=action_set_visible)
        except:
            parse_node_descendants(node=DOM_root_node, action=action_set_visible)
            action_elements["observation highlight"] = None

        action_elements["retained element ids"] = self.parse_actions_to_element_ids(action_elements["action"])

    def parse_action_from_action_candidates(self, action_elements):
        if "action" in action_elements.keys():
            return action_elements
        assert any("action candidates" in k for k in action_elements.keys())
        action_candidates_key = [k for k in action_elements.keys() if "action candidates" in k][0]
        def parse_reasons_and_actions(input_string):
            pattern = r'- reason: \[(.*?)\]\s*(?:- action: \[(.*?)\])?\s*(?:\n|\Z)'

            matches = re.findall(pattern, input_string, re.DOTALL)

            parsed_data = []
            for match in matches:
                reason = match[0].strip()
                action = match[1].strip()
                if reason and action:
                    parsed_data.append({'reason': reason, 'action': action})

            return parsed_data
        action_elements[action_candidates_key] = parse_reasons_and_actions(action_elements[action_candidates_key])
        return action_elements

    def predict_action(self, criticism_elements):
        if self.config.debug > 1:
            action_elements = {k: "" for k in self.config.output}
            human_input = input("ACTION: ")
            action_elements["action"] = human_input
            return [action_elements]
        
        self.pre_process_atomic_actions()
        instruction = self.get_actor_instruction()
        online_input = self.get_online_input(criticism_elements=criticism_elements)
        max_generation_attempts = getattr(
            self.config,
            "max_generation_attempts",
            getattr(self.config.others, "max_generation_attempts", 8),
        )
        max_generation_attempts = max(1, int(max_generation_attempts))
        model_response_list = []
        action_element_list = []
        for _ in range(self.config.number):
            get_valid_actions = False
            repetitive_note = False
            invalid_actions = False
            generation_attempts = 0
            while not get_valid_actions and generation_attempts < max_generation_attempts:
                generation_attempts += 1
                if repetitive_note:
                    model_response = self.call_model_with_message(system_prompt=instruction+"\nGenerating the command `note [{}]` will be severely punished! Don't generate repetitive notes!".format(getattr(self, "note_buffer", "")), messages=self.arrange_message_for_model(online_input))
                elif invalid_actions:
                    model_response = self.call_model_with_message(system_prompt=instruction+"\nGenerating the command `{}` will be severely punished! Don't generate invalid actions! We don't have that element id in the current observation!".format(invalid_action_str), messages=self.arrange_message_for_model(online_input))
                else:
                    # LLM Actor prompt
                    # logger.debug(f"Calling model with instruction system_prompt: {instruction}")
                    # logger.debug(f"Calling model with instruction messages: {self.arrange_message_for_model(online_input)}")
                    model_response = self.call_model_with_message(system_prompt=instruction, messages=self.arrange_message_for_model(online_input))
                action_elements = self.parse_elements(text=model_response, key_list=self.config.output)
                action_elements = self.parse_action_from_action_candidates(action_elements=action_elements)
                assert not ("action" in action_elements.keys() and any("action candidates" in k for k in action_elements.keys()))
                if "action" in action_elements.keys():
                    if self.are_valid_actions(action_elements["action"]):
                        note_buffer = getattr(self, "note_buffer", "")
                        if note_buffer and f"note [{note_buffer}" in action_elements["action"]:
                            print(f"Repetitive note: {note_buffer}")
                            repetitive_note = True
                            continue
                        get_valid_actions = True
                        action_elements["input"] = online_input
                        model_response_list.append(model_response)
                        action_element_list.append(action_elements)
                    else:
                        invalid_action_str = action_elements["action"]
                        print(f"Invalid actions: {invalid_action_str}")
                        invalid_actions = True
                elif any("action candidates" in k for k in action_elements.keys()):
                    action_candidates_key = [k for k in action_elements.keys() if "action candidates" in k][0]
                    if isinstance(action_elements[action_candidates_key], str):
                        continue
                    filtered_action_candidates = []
                    note_buffer = getattr(self, "note_buffer", "")
                    for action_reason_pair in action_elements[action_candidates_key]:
                        action = action_reason_pair["action"]
                        reason = action_reason_pair["reason"]
                        if self.are_valid_actions(action):
                            if note_buffer and f"note [{note_buffer}" in action:
                                print(f"Repetitive note: {note_buffer}")
                                repetitive_note = True
                                continue
                            filtered_action_candidates.append({'reason': reason, 'action': action})
                        else:
                            invalid_action_str = action
                            print(f"Invalid actions: {invalid_action_str}")
                            invalid_actions = True
                    if filtered_action_candidates:
                        action_elements[action_candidates_key] = filtered_action_candidates
                        get_valid_actions = True
                        action_elements["input"] = online_input
                        model_response_list.append(model_response)
                        action_element_list.append(action_elements)
                else:
                    raise NotImplementedError("You have to generate either action or action candidates.")
            if not get_valid_actions:
                failure_reason = (
                    f"Failed to produce a valid action after {generation_attempts} attempts."
                )
                print(
                    f"Warning: Actor failed to produce a valid action after "
                    f"{generation_attempts} attempts. Falling back to stop."
                )
                model_response_list.append(f"[fallback] {failure_reason}")
                action_element_list.append(
                    self._build_retry_exhausted_action(
                        instruction=instruction,
                        online_input=online_input,
                        attempts=generation_attempts,
                    )
                )
        # if self.config.number != 1:
        if True:
            for identity in self.identities:
                identity_instruction = identity.get_instruction() if identity.get_instruction() else instruction
                identity_online_input = identity.get_online_input() if identity.get_online_input() else online_input
                get_valid_actions = False
                invalid_actions = False
                generation_attempts = 0
                while not get_valid_actions and generation_attempts < max_generation_attempts:
                    generation_attempts += 1
                    if invalid_actions:
                        model_response, action_elements = identity.get_action(identity_instruction+"\nGenerating the command `{}` will be severely punished! Don't generate invalid actions! We don't have that element id in the current observation!".format(invalid_action_str), identity_online_input)
                    else:
                        model_response, action_elements = identity.get_action(identity_instruction, identity_online_input)      
                    if self.are_valid_actions(action_elements["action"]):
                        get_valid_actions = True
                        model_response_list.append(model_response)
                        action_element_list.append(action_elements)
                    else:
                        invalid_action_str = action_elements["action"]
                        print(f"Invalid actions: {invalid_action_str}")
                        invalid_actions = True
                if not get_valid_actions:
                    identity_name = getattr(identity.config, "name", identity.__class__.__name__)
                    print(
                        f"Warning: Identity '{identity_name}' failed to produce a valid "
                        f"action after {generation_attempts} attempts and will be skipped."
                    )
        
        self.verbose(instruction=instruction, online_input=online_input, model_response_list=model_response_list, action_element_list=action_element_list)

        if self.config.others.debug or self.config.debug:
            for i in range(len(action_element_list)):
                human_input = input(f"ACTION {i}: ")
                if human_input != "":
                    action_element_list[i]["action"] = human_input

        return action_element_list
    
    def finalize_action(self, action_elements):
        self.get_observation_highlight(action_elements=action_elements)
        action = action_elements["action"]
        navigation_action = self.planning(action=action)
        if navigation_action:
            action_elements["navigation action"] = navigation_action
        action = self.take_note(action)
        action_elements["action"] = action
        navigation_action = self.go_home(action=action)
        if navigation_action:
            action_elements["navigation action"] = navigation_action
        return action_elements

class Critic(Agent):
    def __init__(self, config, objective, prompt_template):
        super().__init__(config, objective, prompt_template)
        self.instruction = None
        self.actor_basic_info_dict = None

        self.output_play_path = os.path.join(CURRENT_DIR, f"play-{self.config.others.logname}.txt") if getattr(self.config.others, "logname", "") != "" else os.path.join(CURRENT_DIR, f"play.txt")
        self.output_trash_path = os.path.join(CURRENT_DIR, f"trash-{self.config.others.logname}.txt") if getattr(self.config.others, "logname", "") != "" else os.path.join(CURRENT_DIR, f"trash.txt")

    def verbose(self, instruction, online_input, model_response):
        VERBOSE_TO_CONTENT_MAP = {
            "url": self.online_interaction["url"],
            "objective": self.objective,
            "instruction": instruction,
            "online input": "\n".join([i[1] for i in online_input if i[0]=="text"]),
            "response": model_response
        }
        if self.config.others.verbose > 0 and self.config.verbose > 0:
            with open(self.output_trash_path, "a") as af:
                af.write("-"*32+"CRITIC"+"-"*32+"\n")
            for t in self.config.trash:
                content = VERBOSE_TO_CONTENT_MAP[t]
                with open(self.output_trash_path, "a") as af:
                    af.write(f"{t.upper()}:\n{content}\n\n")

    def update_actor_basic_info(self, **actor_basic_info_dict):
        self.actor_basic_info_dict = actor_basic_info_dict

    def get_output_specifications(self):
        return build_output_specifications(self.config.output, character=getattr(self.config, "character", None))

    def get_critic_instruction(self):
        if self.instruction:
            return self.instruction
        instruction = self.prompt_template["instruction_template"]
        output_specifications = self.get_output_specifications()
        instruction = instruction.replace("{output_specifications}", output_specifications)
        instruction = instruction.replace("{planning_specifications}", self.actor_basic_info_dict["planning_specifications"])
        instruction = instruction.replace("{navigation_specifications}", self.actor_basic_info_dict["navigation_specifications"])
        self.instruction = instruction
        return self.instruction
    
    def get_online_input(self):
        input_template = self.prompt_template["input_template"]
        input_prefix, input_suffix = input_template.split("{input}")
        # ["objective", "previous plans", "interaction history", "step", "current observation"]
        INPUT_TYPE_TO_CONTENT_MAP = {
            "step": self.actor_basic_info_dict["step"],
            "objective": self.objective,
            "previous plans": self.actor_basic_info_dict["previous_plans"],
            "interaction history": self.actor_basic_info_dict["interaction_history"],
            "current observation": self.get_observation_text(),
            "current visual observation": self.get_observation_image()
        }
        input_list = []
        for input_type in self.config.input:
            input_content = None
            if input_type == "current visual observation":
                continue
            elif input_type in INPUT_TYPE_TO_CONTENT_MAP.keys():
                input_content = INPUT_TYPE_TO_CONTENT_MAP[input_type]
            if input_content and isinstance(input_content, str):
                input_list.append(("text", f"{input_type.upper()}:\n{input_content}\n"))
            elif input_content and isinstance(input_content, list):
                input_list.append(("text", f"{input_type.upper()}:\n"))
                input_list += input_content if len(input_content) > 0 else ["N/A"]

        if "image" in self.config.current_observation.type:
            input_type = "current visual observation"
            input_list.append(("text", f"{input_type.upper()}:\n"))
            input_list.append(("image", INPUT_TYPE_TO_CONTENT_MAP["current visual observation"]))

        return self.prune_message_list(message_list=[("text", input_prefix)] + input_list + [("text", input_suffix)])

    def get_criticism_elements(self):
        if not self.config.mode:
            return {}
        if self.config.debug > 1:
            criticism_elements = {k: random.choice(["I don't think the task is finished. Don't issue identical actions like taking the same notes. It's annoying. Continue.", "You have make a reasoning mistake. Continue.", "You have missed important details on this page. Continue.", "You don't follow the task requirements. Continue.", "The task assigner might just want to challenge you to answer no and there might be no answer for this brain teaser question. Who knows?", "You should break down the task by using the planning commands.", "You have not gone over all the relevant pages. Continue."]) for k in self.config.output}
            # criticism_elements = {k: input(f"{k.upper()}: ") for k in self.config.output}
            return criticism_elements
        
        instruction = self.get_critic_instruction()
        online_input = self.get_online_input()
        model_response = self.call_model_with_message(system_prompt=instruction, messages=self.arrange_message_for_model(online_input))
        self.verbose(instruction=instruction, online_input=online_input, model_response=model_response)

        criticism_elements = self.parse_elements(text=model_response, key_list=self.config.output) # key_list=self.config.output)
        criticism_elements["input"] = online_input

        if self.config.others.debug or self.config.debug:
            for k in self.config.output:
                human_input = input(f"{k.upper()}: ")
                if not human_input == "":
                    criticism_elements[k] = human_input
        
        return criticism_elements

class Judge(Agent):
    def __init__(self, config, objective, prompt_template):
        super().__init__(config, objective, prompt_template)
        self.instruction = None
        self.actor_basic_info_dict = None

        self.output_play_path = os.path.join(CURRENT_DIR, f"play-{self.config.others.logname}.txt") if getattr(self.config.others, "logname", "") != "" else os.path.join(CURRENT_DIR, f"play.txt")
        self.output_trash_path = os.path.join(CURRENT_DIR, f"trash-{self.config.others.logname}.txt") if getattr(self.config.others, "logname", "") != "" else os.path.join(CURRENT_DIR, f"trash.txt")
    
    def update_actor_basic_info(self, **actor_basic_info_dict):
        self.actor_basic_info_dict = actor_basic_info_dict

    def get_judge_instruction(self):
        if self.instruction:
            return self.instruction
        instruction = self.prompt_template["instruction_template"]
        output_specifications = self.get_output_specifications()
        instruction = instruction.replace("{output_specifications}", output_specifications)
        instruction = instruction.replace("{planning_specifications}", self.actor_basic_info_dict["planning_specifications"])
        instruction = instruction.replace("{navigation_specifications}", self.actor_basic_info_dict["navigation_specifications"])
        self.instruction = instruction
        return self.instruction
    
    def get_online_input(self, action_element_list):
        input_template = self.prompt_template["input_template"]
        input_prefix, input_suffix = input_template.split("{input}")
        INPUT_TYPE_TO_CONTENT_MAP = {
            "step": self.actor_basic_info_dict["step"],
            "objective": self.objective,
            "previous plans": self.actor_basic_info_dict["previous_plans"],
            "interaction history": self.actor_basic_info_dict["interaction_history"],
            "current observation": self.get_observation_text(),
            "current visual observation": self.get_observation_image(),
            "action choices": "\n\n".join(["|\taction [{}]:\n{}\n|\treason for action [{}]:\n{}".format(i, action_element["action"], i, action_element.get("reason", "N/A")) for i, action_element in enumerate(action_element_list)])
        }
        input_list = []
        for input_type in self.config.input:
            input_content = None
            if input_type == "current visual observation":
                continue
            elif input_type in INPUT_TYPE_TO_CONTENT_MAP.keys():
                input_content = INPUT_TYPE_TO_CONTENT_MAP[input_type]
            if input_content and isinstance(input_content, str):
                input_list.append(("text", f"{input_type.upper()}:\n{input_content}\n"))
            elif input_content and isinstance(input_content, list):
                input_list.append(("text", f"{input_type.upper()}:\n"))
                input_list += input_content if len(input_content) > 0 else ["N/A"]

        if "image" in self.config.current_observation.type:
            input_type = "current visual observation"
            input_list.append(("text", f"{input_type.upper()}:\n"))
            input_list.append(("image", INPUT_TYPE_TO_CONTENT_MAP["current visual observation"]))

        return self.prune_message_list(message_list=[("text", input_prefix)] + input_list + [("text", input_suffix)])
    
    def verbose(self, instruction, online_input, model_response):
        VERBOSE_TO_CONTENT_MAP = {
            "url": self.online_interaction["url"],
            "objective": self.objective,
            "instruction": instruction,
            "online input": "\n".join([i[1] for i in online_input if i[0]=="text"]),
            "response": model_response
        }
        if self.config.others.verbose > 0 and self.config.verbose > 0:
            with open(self.output_trash_path, "a") as af:
                af.write("-"*32+"JUDGE"+"-"*32+"\n")
            for t in self.config.trash:
                content = VERBOSE_TO_CONTENT_MAP[t]
                with open(self.output_trash_path, "a") as af:
                    af.write(f"{t.upper()}:\n{content}\n\n")

    def flatten_action_element_list(self, action_element_list):
        new_action_element_list = []
        for action_element in action_element_list:
            if any("action candidates" in k for k in action_element.keys()):
                action_candidates_key = [k for k in action_element.keys() if "action candidates" in k][0]
                new_action_element = copy.deepcopy(action_element)
                for action_reason_pair in action_element[action_candidates_key]:
                    new_action_element["action"] = action_reason_pair["action"]
                    new_action_element["reason"] = action_reason_pair["reason"]
                    new_action_element_list.append(copy.deepcopy(new_action_element))
            else:
                new_action_element_list.append(action_element)
        random.shuffle(new_action_element_list)

        return new_action_element_list
    
    def judge(self, action_element_list):
        action_element_list = self.flatten_action_element_list(action_element_list)
        if not self.config.mode or self.config.debug > 1:
            return action_element_list[0], {}
        if all(action_elements["action"]==action_element_list[0]["action"] for action_elements in action_element_list):
            return action_element_list[0], {}
        
        def deduplicate_action_element_list_strict(lst): # deduplicate, remove action_elements with only note or stop command
            seen = set()
            note_list = []
            stop_list = []
            deduplicated_list = []
    
            for i, item in enumerate(lst):
                item = copy.deepcopy(item)
                action_list = self.parse_str_to_action_list(item["action"], self.actor_basic_info_dict["planning_command"]+self.actor_basic_info_dict["navigation_command"])
                note_list.append([])
                none_note_stop_action_list = []
                for a in action_list:
                    if a.startswith("stop ["):
                        stop_list.append((a, i))
                    elif a.startswith("note ["):
                        note_list[-1].append(a)
                    else:
                        none_note_stop_action_list.append(a)
                item["action"] = "\n".join(none_note_stop_action_list)
                if item["action"] and item["action"] not in seen:
                    seen.add(item["action"])
                    deduplicated_list.append(item)
            note_list = [("\n".join(notes), i) for i, notes in enumerate(note_list)]
            return note_list, stop_list, deduplicated_list
          
        def deduplicate_action_element_list(lst): # deduplicate, remove action_elements with only note or stop command
            seen = set()
            deduplicated_list = []
    
            for item in lst:
                item = copy.deepcopy(item)
                if item["action"] and item["action"] not in seen:
                    seen.add(item["action"])
                    deduplicated_list.append(item)
            return deduplicated_list

        if hasattr(self.config, "strict") and self.config.strict:
            note_list, stop_list, deduplicated_action_element_list = deduplicate_action_element_list_strict(action_element_list)
            if len(stop_list) >= 0.6 * len(action_element_list):
                stop_action_choice = max([s[0] for s in stop_list], key=len)
                stop_action_id = [s[1] for s in stop_list if s[0]==stop_action_choice][0]
                return action_element_list[stop_action_id], {}
            if not deduplicated_action_element_list:
                note_action_choice = max([n[0] for n in note_list], key=len)
                note_action_id = [n[1] for n in note_list if n[0]==note_action_choice][0]
                action_elements = action_element_list[note_action_id]
                action_elements["action"] = note_action_choice
                return action_elements, {}
            elif len(deduplicated_action_element_list) == 1:
                action_elements = deduplicated_action_element_list[0]
                note_action_choice = max([n[0] for n in note_list], key=len)
                action_elements["action"] = note_action_choice + "\n" + action_elements["action"]
                return action_elements, {}
        else:
            deduplicated_action_element_list = deduplicate_action_element_list(action_element_list)
        
        instruction = self.get_judge_instruction()
        online_input = self.get_online_input(deduplicated_action_element_list)
        model_response = self.call_model_with_message(system_prompt=instruction, messages=self.arrange_message_for_model(online_input))
        self.verbose(instruction=instruction, online_input=online_input, model_response=model_response)

        judgement_elements = self.parse_elements(text=model_response, key_list=self.config.output) # key_list=self.config.output)
        judgement_elements["input"] = online_input

        if self.config.others.debug or self.config.debug:
            for k in self.config.output:
                human_input = input(f"{k.upper()}: ")
                if not human_input == "":
                    judgement_elements[k] = human_input

        try:
            action_selection = int(re.search(r'\d+', judgement_elements["action selection"]).group())
            selected_action_elements = deduplicated_action_element_list[action_selection]
            if hasattr(self.config, "strict") and self.config.strict:
                note_action_choice = max([n[0] for n in note_list], key=len)
                if note_action_choice:
                    selected_action_elements["action"] = note_action_choice + "\n" + selected_action_elements["action"]
            return selected_action_elements, judgement_elements
        except:
            return action_element_list[0], judgement_elements

class AgentOccam:
    # ── regex for ISP type-action detection ──────────────────────────────────
    _TYPE_RE = re.compile(
        r'type \[(\d+)\] \[(.*?)\] \[(\d+)\]', re.DOTALL
    )
    _GHERKIN_FILL_RE = re.compile(
        r"^\s*I\s+(?:fill\s+in|enter)\s+(?P<label>.+?)(?:\s+(?:with|as)\s+['\"].*)?\s*$",
        re.IGNORECASE,
    )

    def __init__(self,
                 config = None,
                 prompt_dict: Dict = None,
                 ):
        self.config = config
        self.prompt_dict = {} if prompt_dict is None else prompt_dict

        self.objective = None
        self.online_observation = None
        self.online_url = None
        self.actor = None
        self.critic = None

        self.trajectory = []

    def get_refined_objective(self):
        model_response = call_claude(self.root_prompt_template["objective_rephrasing_query"].replace("{objective}", self.objective))
        objective_match = re.search(r'REFINED OBJECTIVE:\s*(.*?)\s*(?=\n[A-Z]|$)', model_response, re.DOTALL) 
        self.objective_refined = objective_match.group(1) if objective_match else None
        
    def get_observation_text(self):
        if isinstance(self.online_observation, dict):
            return self.online_observation["text"]
        else:
            return self.online_observation
    
    def init_actor(self):
        self.config.actor.others = self.config.others ## pass others config to actor
        if len(self.sites) > 1:
            self.config.actor.navigation_command += ["go_home"]
        self.actor = Actor(
            config=self.config.actor,
            objective=self.objective,
            prompt_template=self.prompt_dict["actor"],
            plan_tree_node=PlanTreeNode(id=0, type="branch", text=f"Find the solution to \"{self.objective}\"", level=0, url=self.online_url, step=0),
            is_gherkin_task=getattr(self, 'is_gherkin_task', False),
            is_isp_task=getattr(self, 'is_isp_task', False),
        )
        with open(self.actor.output_trash_path, "w") as _:
            pass

    def init_critic(self):
        self.config.critic.others = self.config.others
        self.critic = Critic(
            config=self.config.critic,
            objective=self.objective,
            prompt_template=self.prompt_dict["critic"][self.config.critic.character],
        )
    
    def init_judge(self):
        self.config.judge.others = self.config.others
        self.judge = Judge(
            config=self.config.judge,
            objective=self.objective,
            prompt_template=self.prompt_dict["judge"],
        )
        
    def predict_action(self):
        # Check if using ADK orchestration mode
        if self._should_use_adk_orchestration():
            return self._predict_action_with_adk()

        return self._predict_action_standard_flow()

    def _predict_action_standard_flow(self):
        # Standard Actor-Critic-Judge flow
        print("Using standard Actor-Critic-Judge flow")
        self.critic.update_actor_basic_info(step=self.get_step(), planning_specifications=self.actor.get_planning_specifications(), navigation_specifications=self.actor.get_navigation_specifications(), interaction_history=self.actor.get_interaction_history(interaction_history_config=self.critic.config.interaction_history), previous_plans=self.actor.get_previous_plans(verbose=True))
        criticism_elements = self.critic.get_criticism_elements() if not self.get_step()==0 else {}
        action_element_list = self.actor.predict_action(criticism_elements=criticism_elements)
        self.judge.update_actor_basic_info(step=self.get_step(), planning_specifications=self.actor.get_planning_specifications(), navigation_specifications=self.actor.get_navigation_specifications(), interaction_history=self.actor.get_interaction_history(interaction_history_config=self.judge.config.interaction_history), previous_plans=self.actor.get_previous_plans(verbose=True), planning_command=self.actor.config.planning_command, navigation_command=self.actor.config.navigation_command)
        selected_action_elements, judgement_elements = self.judge.judge(action_element_list)
        selected_action_elements = self.actor.finalize_action(selected_action_elements)
        return {**selected_action_elements, **{"critic:"+k: criticism_elements[k] for k in criticism_elements.keys()}, **{"judge:"+k: judgement_elements[k] for k in judgement_elements.keys()}}, action_element_list
    
    def _should_use_adk_orchestration(self):
        """Check if ADK orchestration should be used."""
        return (
            self.actor.model_family == 'adk' and
            ADK_AVAILABLE and
            hasattr(self.config.actor, 'adk_config') and
            getattr(self.config.actor.adk_config, 'orchestration_mode', None) is not None
        )
    
    def _predict_action_with_adk(self):
        """
        ADK-powered Actor-Critic-Judge orchestration.
        Uses ADK's agent coordination while maintaining compatibility with existing flow.
        """
        try:
            from google.adk.agents import LlmAgent
            from google.adk.runners import Runner
            from google.adk.sessions import InMemorySessionService
            from google.genai import types
        except ImportError:
            print("Warning: google-adk not available, falling back to standard flow")
            return self._predict_action_standard_flow()
        
        # Get orchestration mode
        orchestration_mode = getattr(self.config.actor.adk_config, 'orchestration_mode', 'sequential')
        
        if orchestration_mode == 'sequential':
            print("Using ADK sequential orchestration")
            return self._adk_sequential_orchestration()
        elif orchestration_mode == 'parallel':
            return self._adk_parallel_orchestration()
        else:
            print(f"Unknown orchestration mode: {orchestration_mode}, using sequential")
            return self._adk_sequential_orchestration()
    
    def _adk_sequential_orchestration(self):
        """
        Sequential ADK orchestration: Critic -> Actor -> Judge.
        Each agent uses ADK internally but follows the standard flow.
        """
        # Step 1: Critic evaluation (if not first step)
        criticism_elements = {}
        if not self.get_step() == 0 and self.config.critic.mode:
            self.critic.update_actor_basic_info(
                step=self.get_step(),
                planning_specifications=self.actor.get_planning_specifications(),
                navigation_specifications=self.actor.get_navigation_specifications(),
                interaction_history=self.actor.get_interaction_history(
                    interaction_history_config=self.critic.config.interaction_history
                ),
                previous_plans=self.actor.get_previous_plans(verbose=True)
            )
            
            # Use ADK for critic if configured
            if self.critic.model_family == 'adk':
                criticism_elements = self._adk_get_criticism()
            else:
                criticism_elements = self.critic.get_criticism_elements()
        
        # Step 2: Actor action prediction with ADK
        if self.actor.model_family == 'adk':
            action_element_list = self._adk_predict_actor_action(criticism_elements)
        else:
            action_element_list = self.actor.predict_action(criticism_elements=criticism_elements)
        
        # Step 3: Judge selection (if multiple actions or judge mode enabled)
        judgement_elements = {}
        if self.config.judge.mode or len(action_element_list) > 1:
            self.judge.update_actor_basic_info(
                step=self.get_step(),
                planning_specifications=self.actor.get_planning_specifications(),
                navigation_specifications=self.actor.get_navigation_specifications(),
                interaction_history=self.actor.get_interaction_history(
                    interaction_history_config=self.judge.config.interaction_history
                ),
                previous_plans=self.actor.get_previous_plans(verbose=True),
                planning_command=self.actor.config.planning_command,
                navigation_command=self.actor.config.navigation_command
            )
            
            # Use ADK for judge if configured
            if self.judge.model_family == 'adk':
                selected_action_elements, judgement_elements = self._adk_judge_actions(action_element_list)
            else:
                selected_action_elements, judgement_elements = self.judge.judge(action_element_list)
        else:
            selected_action_elements = action_element_list[0]
        
        # Finalize action
        selected_action_elements = self.actor.finalize_action(selected_action_elements)
        
        return {
            **selected_action_elements,
            **{"critic:"+k: criticism_elements[k] for k in criticism_elements.keys()},
            **{"judge:"+k: judgement_elements[k] for k in judgement_elements.keys()}
        }, action_element_list
    
    def _adk_parallel_orchestration(self):
        """
        Parallel ADK orchestration: Run critic and multiple actors concurrently.
        (Placeholder for future implementation)
        """
        # For now, fall back to sequential
        print("Note: Parallel ADK orchestration not yet implemented, using sequential mode")
        return self._adk_sequential_orchestration()
    
    def _adk_get_criticism(self):
        """
        Use ADK to get critic feedback with enhanced context.
        """
        # Get instruction and input for critic
        instruction = self.critic.get_critic_instruction()
        online_input = self.critic.get_online_input()
        
        # Convert to ADK format and call
        prompt = self.critic.arrange_message_for_model(online_input)
        
        try:
            # Call ADK with critic-specific context
            model_response = self.critic.call_model_with_message(
                system_prompt=instruction,
                messages=online_input
            )
            
            # Parse response into criticism elements
            criticism_elements = self.critic.parse_elements(
                text=model_response,
                key_list=self.critic.config.output
            )
            
            return criticism_elements
        except Exception as e:
            print(f"ADK critic call failed: {e}, using empty criticism")
            return {}
    
    def _adk_predict_actor_action(self, criticism_elements):
        """
        Use ADK to predict actor actions with tool support.
        """
        # Standard actor prediction but leveraging ADK's capabilities
        action_element_list = self.actor.predict_action(criticism_elements=criticism_elements)
        return action_element_list
    
    def _adk_judge_actions(self, action_element_list):
        """
        Use ADK to judge between multiple action candidates.
        """
        # Standard judge selection but leveraging ADK's reasoning
        selected_action_elements, judgement_elements = self.judge.judge(action_element_list)
        return selected_action_elements, judgement_elements
    
    def update_online_state(self, url, observation):
        self.online_url = url
        self.online_observation = observation

    def get_step(self):
        return self.actor.get_step()
    
    def is_navigation(self, action):
        return self.actor.is_navigation(action=action)
    
    def get_actor_active_plan(self):
        return self.actor.get_active_plan()
    
    def get_trajectory(self):
        return self.trajectory

    def act(self, objective, env):
        self.objective = objective
        self.sites = env.get_sites()
        self.is_gherkin_task = hasattr(env, 'gherkin_scenario') and env.gherkin_scenario is not None
        self.is_isp_task = getattr(env, 'is_isp_task', False)
        observation = env.observation()
        url = env.get_url()
        self.update_online_state(url=url, observation=observation)
        self.init_actor()
        self.init_critic()
        self.init_judge()
        while not env.done():
            observation = env.observation()
            url = env.get_url()
            self.update_online_state(url=url, observation=observation)
            self.actor.update_online_state(url=url, observation=observation)
            self.critic.update_online_state(url=url, observation=observation)
            self.judge.update_online_state(url=url, observation=observation)
            action_elements, action_element_list = self.predict_action()
            action = action_elements["action"]
            navigation_action = action_elements["action"] if not action_elements.get("navigation action", "") else action_elements.get("navigation action", "")
            status = env.step(navigation_action)
            if navigation_action and self.is_navigation(action=navigation_action) and status == False: # means invalid action
                flaw_node = self.actor.active_node
                flaw_node.note.append(f"STEP {self.get_step()}: You generate action \"{action}\", which has INVALID syntax. Strictly follow the action specifications.")          
            DOCUMENTED_INTERACTION_ELEMENT_KEY_TO_CONTENT_MAP = {
                "observation": observation,
                "action": action,
                "url": url,
                "plan": self.get_actor_active_plan(),
                "reason": action_elements.get("reason", ""),
                "observation highlight": action_elements.get("observation highlight", ""),
                "retained element ids": action_elements.get("retained element ids", []),
                "observation summary": action_elements.get("observation description", "")                  
            }
            self.actor.update_history(**DOCUMENTED_INTERACTION_ELEMENT_KEY_TO_CONTENT_MAP)
            self.actor.del_observation_node()
            assert self.actor.equal_history_length()

            if len(action_element_list) > 1:
                if self.config.others.logging:
                    self.log_step(
                        status=status if "status" in locals() and isinstance(status, dict) else env.status(),
                        plan=self.get_actor_active_plan(),
                        **action_elements,
                        **{f"actor {i}:{k}": _action_elements[k] for i, _action_elements in enumerate(action_element_list) for k in _action_elements.keys() if k != "input" and k != "instruction"}
                    )
            else:
                if self.config.others.logging:
                    self.log_step(
                        status=status if "status" in locals() and isinstance(status, dict) else env.status(),
                        plan=self.get_actor_active_plan(),
                        **action_elements,
                    )

        return status if "status" in locals() and isinstance(status, dict) else env.status()
    
    def log_step(self, status, **kwargs):
        def serialize_message_list(message_list):
            if not isinstance(message_list, list):
                return message_list
            return "".join([m[1] for m in message_list if m[0]=="text"])
        data_to_log = {}
        data_to_log['objective'] = self.objective
        data_to_log['url'] = self.online_url
        data_to_log['observation'] = self.get_observation_text()
        for (k, v) in status.items():
            data_to_log[k] = v
        for k in kwargs.keys():
            try:
                json.dumps(kwargs[k])
                data_to_log[k.replace(" ", "_")] = kwargs[k] if not "input" in k else serialize_message_list(kwargs[k])
            except:
                pass
        self.trajectory.append(data_to_log)

    # ══════════════════════════════════════════════════════════════════════════
    # ISP – task file generation
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _get_obs_text(env) -> str:
        """Extract plain accessibility-tree text from env.obs (handles tuple/dict)."""
        raw = env.obs
        if isinstance(raw, dict):
            text = raw.get("text", "")
            if isinstance(text, (list, tuple)):
                return text[0] if text else ""
            return str(text)
        return str(raw)

    @classmethod
    def _to_plain_data(cls, value):
        if isinstance(value, dict):
            return {k: cls._to_plain_data(v) for k, v in value.items()}
        if isinstance(value, list):
            return [cls._to_plain_data(v) for v in value]
        if hasattr(value, "to_dict") and callable(value.to_dict):
            return cls._to_plain_data(value.to_dict())
        if hasattr(value, "__dict__") and not isinstance(value, (str, bytes)):
            return {k: cls._to_plain_data(v) for k, v in vars(value).items()}
        return value

    @staticmethod
    def _normalize_field_reference(label: str) -> str:
        text = re.sub(r"\s+", " ", str(label or "")).strip().strip("'\"")
        text = re.sub(r"^\s*(?:the|a|an)\s+", "", text, flags=re.IGNORECASE)
        text = re.sub(
            r"\s+(?:field|textbox|input|box)\s*$",
            "",
            text,
            flags=re.IGNORECASE,
        )
        return text.strip(" :")

    @classmethod
    def _extract_fill_step_labels(cls, when_steps: list) -> list:
        fields: list = []
        for raw_step in when_steps or []:
            step = str(raw_step)
            match = cls._GHERKIN_FILL_RE.match(step)
            if not match:
                continue

            raw_label = re.sub(r"\s+", " ", match.group("label")).strip()
            normalized = cls._normalize_field_reference(raw_label)
            display = normalized or raw_label
            keywords = [display.lower()]
            raw_lower = raw_label.lower()
            if raw_lower and raw_lower not in keywords:
                keywords.append(raw_lower)

            fields.append({
                "display": display,
                "keywords": keywords,
            })
        return fields

    @staticmethod
    def _dedupe_field_label(label: str, counts: dict, fallback: str) -> str:
        base = str(label or fallback).strip() or str(fallback)
        count = counts.get(base, 0) + 1
        counts[base] = count
        if count == 1:
            return base
        return f"{base} ({count})"

    @classmethod
    def _find_best_testcase_label_match(
        cls,
        step_field: str,
        case_inputs: dict,
        field_label_map: dict,
    ) -> str | None:
        if not step_field:
            return None

        exact_matches: list[tuple[int, str]] = []
        fuzzy_matches: list[tuple[int, int, str]] = []

        for index, label in enumerate(case_inputs.keys()):
            keywords = field_label_map.get(label, [label.lower()])
            candidates = [cls._normalize_field_reference(label).lower()]
            candidates.extend(
                cls._normalize_field_reference(keyword).lower()
                for keyword in keywords
                if keyword
            )
            candidates = [candidate for candidate in dict.fromkeys(candidates) if candidate]

            if any(candidate == step_field for candidate in candidates):
                exact_priority = 0 if cls._normalize_field_reference(label).lower() == step_field else 1
                exact_matches.append((exact_priority, label))
                continue

            for candidate in candidates:
                if candidate in step_field or step_field in candidate:
                    fuzzy_matches.append((abs(len(candidate) - len(step_field)), index, label))
                    break

        if exact_matches:
            exact_matches.sort(key=lambda item: (item[0], item[1]))
            return exact_matches[0][1]

        if fuzzy_matches:
            fuzzy_matches.sort(key=lambda item: (item[0], item[1], item[2]))
            return fuzzy_matches[0][2]

        return None

    def generate_isp_task_files(
        self,
        discoveries: list,
        task_config: dict,
        config_file_path: str,
    ) -> list:
        """Generate ISP variant task JSON files from recorded ``type`` actions.

        Called after a normal :meth:`act` run on a task with ``isp.enabled:
        true``.  The *discoveries* list comes from monitoring ``env.step``
        during that run.

        For each generated ISP testcase:

        * Copies the original task JSON.
        * Removes the ``isp`` block (not needed in child tasks).
        * Embeds concrete values into ``gherkin.when`` steps.
        * Adds an ``isp_test_case`` block with the concrete input values and
          the testcase expected outcome.
        * Writes the result to
          ``<config_dir>/isp_tasks/<task_id>_isp_<NN>.json``.

        Parameters
        ----------
        discoveries : list
            ``[{"element_id", "original_value", "obs_text"}, ...]``
        task_config : dict
            Parsed original task JSON (must contain the ``isp`` block).
        config_file_path : str
            Filesystem path to the original task JSON (derives output dir).

        Returns
        -------
        list of str
            Paths to the generated task JSON files.
        """
        import copy
        import os
        from AgentOccam.isp_generator import FieldAnalyzer, ISPGenerator

        global_isp_cfg = self._to_plain_data(getattr(self.config, "isp", {}))
        task_isp_cfg = task_config.get("isp", {})
        isp_cfg = {}
        if isinstance(global_isp_cfg, dict):
            isp_cfg.update(global_isp_cfg)
        if isinstance(task_isp_cfg, dict):
            isp_cfg.update(task_isp_cfg)

        field_hints = isp_cfg.get("field_hints", [])
        max_fields = int(isp_cfg.get("max_fields") or 10)
        max_cases = int(
            isp_cfg.get("max_test_cases")
            or isp_cfg.get("max_combinations")
            or isp_cfg.get("max_partitions_per_field")
            or 5
        )
        isp_model = isp_cfg.get("isp_model", "gemini-2.5-flash")
        gherkin_ctx = task_config.get("gherkin", {})
        gherkin_when = list(gherkin_ctx.get("when", []))
        fill_steps = self._extract_fill_step_labels(gherkin_when)

        class _ISPCfg:
            pass
        merged_cfg = _ISPCfg()
        merged_cfg.max_test_cases = max_cases
        merged_cfg.isp_model = isp_model
        merged_cfg.expected_outcomes_by_category = (
            isp_cfg.get("expected_outcomes_by_category", {})
        )

        isp_gen = ISPGenerator(
            isp_config=merged_cfg,
            actor_config=self.config.actor,
        )

        # ── De-duplicate & cap fields ─────────────────────────────────────────
        seen: set = set()
        unique: list = []
        for d in discoveries:
            if d["element_id"] not in seen:
                seen.add(d["element_id"])
                unique.append(d)
        unique = unique[:max_fields]

        if not unique:
            print("[ISP] No type actions recorded — skipping task file generation.")
            return []

        # ── Prepare field metadata and labels ─────────────────────────────────
        field_label_map:  dict = {}   # label -> list of match keywords
        label_counts: dict = {}
        prepared_fields: list = []

        for idx, d in enumerate(unique):
            meta = FieldAnalyzer.extract(
                d["element_id"], d["obs_text"], field_hints=field_hints
            )
            meta.original_value = d["original_value"]
            task_field = fill_steps[idx] if idx < len(fill_steps) else None
            if task_field and task_field.get("display"):
                meta.label = task_field["display"]
                normalized_task_label = task_field["display"].lower()
                if "password" in normalized_task_label:
                    meta.input_type = "password"
                elif "email" in normalized_task_label:
                    meta.input_type = "email"
                elif any(token in normalized_task_label for token in ["number", "amount", "count", "qty", "quantity"]):
                    meta.input_type = "number"

            label = self._dedupe_field_label(
                task_field["display"] if task_field else meta.label or d["element_id"],
                label_counts,
                fallback=d["element_id"],
            )

            keywords = []
            if task_field:
                keywords.extend(task_field.get("keywords", []))
            if meta.label:
                keywords.append(meta.label.lower())
                keywords.append(self._normalize_field_reference(meta.label).lower())
            keywords.append(label.lower())
            keywords.append(self._normalize_field_reference(label).lower())
            for hint in field_hints:
                kws = [kw.lower() for kw in hint.get("label_keywords", [])]
                if any(
                    kw in label.lower()
                    or label.lower() in kw
                    or (meta.label and (kw in meta.label.lower() or meta.label.lower() in kw))
                for kw in kws):
                    keywords = kws + keywords
                    break
            field_label_map[label] = list(dict.fromkeys([kw for kw in keywords if kw]))
            prepared_fields.append((label, meta))

        ordered_labels = [label for label, _ in prepared_fields]
        generated_test_cases = isp_gen.generate_test_cases({
            label: meta
            for label, meta in prepared_fields
        },
            gherkin_context=gherkin_ctx,
            max_cases=max_cases,
        )
        print(f"[ISP] {len(generated_test_cases)} testcase(s) generated.")
        if not generated_test_cases:
            print("[ISP] No ISP testcases generated — skipping task file generation.")
            return []

        # ── Output directory ──────────────────────────────────────────────────
        config_dir       = os.path.dirname(os.path.abspath(config_file_path))
        out_dir          = os.path.join(config_dir, "isp_tasks")
        os.makedirs(out_dir, exist_ok=True)

        original_task_id = task_config.get("task_id", "task")
        generated_paths: list = []

        def _extract_case_value(payload) -> str:
            if isinstance(payload, dict):
                return str(payload.get("value", ""))
            return str(payload or "")

        for idx, test_case in enumerate(generated_test_cases, start=1):
            nn       = str(idx).zfill(2)
            new_id   = f"{original_task_id}_isp_{nn}"
            case_name = str(getattr(test_case, "name", "") or f"case {idx}")
            expected = str(getattr(test_case, "expected", "unknown") or "unknown").lower()
            if expected not in {"pass", "fail", "unknown"}:
                expected = "unknown"
            case_inputs = {
                label: str(value)
                for label, value in dict(getattr(test_case, "inputs", {}) or {}).items()
            }

            # Deep copy and strip ISP block
            new_config = copy.deepcopy(task_config)
            new_config["task_id"] = new_id
            new_config.pop("isp", None)

            # Embed concrete values into gherkin.when steps
            new_when: list = []
            for step in gherkin_when:
                step_lower = step.lower()
                modified   = step
                fill_match = self._GHERKIN_FILL_RE.match(step)
                step_field = (
                    self._normalize_field_reference(fill_match.group("label")).lower()
                    if fill_match
                    else None
                )
                matched_label = (
                    self._find_best_testcase_label_match(step_field, case_inputs, field_label_map)
                    if step_field is not None
                    else None
                )
                if matched_label is not None:
                    part_value = _extract_case_value(case_inputs[matched_label])
                    quoted_value = json.dumps(str(part_value), ensure_ascii=False)
                    if re.search(r"\bwith\s+['\"]", modified, re.IGNORECASE):
                        modified = re.sub(
                            r"(\bwith\s+)(['\"]).*?\2",
                            lambda m: f"{m.group(1)}{quoted_value}",
                            modified,
                            count=1,
                            flags=re.IGNORECASE,
                        )
                    else:
                        modified = f"{modified} with {quoted_value}"
                else:
                    for label, payload in case_inputs.items():
                        kws = field_label_map.get(label, [label.lower()])
                        is_keyword_match = any(kw in step_lower for kw in kws if kw)
                        if not is_keyword_match:
                            continue
                        part_value = _extract_case_value(payload)
                        quoted_value = json.dumps(str(part_value), ensure_ascii=False)
                        if re.search(r"\bwith\s+['\"]", modified, re.IGNORECASE):
                            modified = re.sub(
                                r"(\bwith\s+)(['\"]).*?\2",
                                lambda m: f"{m.group(1)}{quoted_value}",
                                modified,
                                count=1,
                                flags=re.IGNORECASE,
                            )
                        else:
                            modified = f"{modified} with {quoted_value}"
                        break
                new_when.append(modified)
            new_config["gherkin"]["when"] = new_when

            # ISP child tasks only need execution steps; remove assertions.
            if "gherkin" in new_config and isinstance(new_config["gherkin"], dict):
                new_config["gherkin"].pop("then", None)

            # ISP test case metadata
            isp_test_case: dict = {
                "_case_name": case_name,
                "_expected": expected,
            }
            for label in ordered_labels:
                isp_test_case[label] = {"value": _extract_case_value(case_inputs.get(label, ""))}
            new_config["isp_test_case"] = isp_test_case

            # ISP child tasks use llm_judge so the LLM can autonomously
            # decide whether the agent handled the test case correctly.
            # The evaluator reads gherkin + isp_test_case at runtime,
            # so no reference_answers block is needed.
            new_config.pop("eval", None)
            new_config["eval"] = {"eval_types": ["llm_judge"]}

            # Write file
            out_path = os.path.join(out_dir, f"{new_id}.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(new_config, f, ensure_ascii=False, indent=2)
            print(f"[ISP] Written: {out_path}  (expected={expected}; case={case_name})")
            generated_paths.append(out_path)

        return generated_paths
