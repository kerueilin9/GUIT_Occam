"""
ADK Tool Definitions for Browser Automation

This module defines ADK-compatible tools for web navigation and interaction.
Tools are organized into toolsets that can be registered with agents.
"""

# Browser automation toolset
BROWSER_AUTOMATION_TOOLS = [
    {
        'name': 'click_element',
        'description': 'Click on a web element by its ID from the observation',
        'parameters': {
            'type': 'object',
            'properties': {
                'element_id': {
                    'type': 'string',
                    'description': 'The ID of the element to click (e.g., "123" from [123] in observation)'
                }
            },
            'required': ['element_id']
        }
    },
    {
        'name': 'type_text',
        'description': 'Type text into an input field',
        'parameters': {
            'type': 'object',
            'properties': {
                'element_id': {
                    'type': 'string',
                    'description': 'The ID of the input element'
                },
                'text': {
                    'type': 'string',
                    'description': 'The text to type'
                },
                'press_enter': {
                    'type': 'boolean',
                    'description': 'Whether to press Enter after typing',
                    'default': True
                }
            },
            'required': ['element_id', 'text']
        }
    },
    {
        'name': 'scroll_page',
        'description': 'Scroll the page up or down',
        'parameters': {
            'type': 'object',
            'properties': {
                'direction': {
                    'type': 'string',
                    'enum': ['up', 'down'],
                    'description': 'Direction to scroll'
                }
            },
            'required': ['direction']
        }
    },
    {
        'name': 'navigate_to',
        'description': 'Navigate to a specific URL',
        'parameters': {
            'type': 'object',
            'properties': {
                'url': {
                    'type': 'string',
                    'description': 'The URL to navigate to'
                }
            },
            'required': ['url']
        }
    },
    {
        'name': 'go_back',
        'description': 'Go back to the previous page in browser history',
        'parameters': {
            'type': 'object',
            'properties': {}
        }
    },
    {
        'name': 'stop_task',
        'description': 'Stop the current task and provide the final answer',
        'parameters': {
            'type': 'object',
            'properties': {
                'answer': {
                    'type': 'string',
                    'description': 'The final answer or result'
                }
            },
            'required': ['answer']
        }
    }
]

# Planning toolset
PLANNING_TOOLS = [
    {
        'name': 'branch_plan',
        'description': 'Create a new sub-plan branching from a parent plan',
        'parameters': {
            'type': 'object',
            'properties': {
                'parent_plan_id': {
                    'type': 'integer',
                    'description': 'The ID of the parent plan node'
                },
                'new_subplan': {
                    'type': 'string',
                    'description': 'Description of the new sub-plan'
                }
            },
            'required': ['parent_plan_id', 'new_subplan']
        }
    },
    {
        'name': 'prune_plan',
        'description': 'Prune the plan tree and resume from a previous plan',
        'parameters': {
            'type': 'object',
            'properties': {
                'resume_plan_id': {
                    'type': 'integer',
                    'description': 'The ID of the plan to resume'
                },
                'reason': {
                    'type': 'string',
                    'description': 'Reason for pruning and resuming'
                }
            },
            'required': ['resume_plan_id', 'reason']
        }
    }
]

# Evaluation toolset for critic
EVALUATION_TOOLS = [
    {
        'name': 'evaluate_action',
        'description': 'Evaluate the quality and correctness of a proposed action',
        'parameters': {
            'type': 'object',
            'properties': {
                'action': {
                    'type': 'string',
                    'description': 'The action to evaluate'
                },
                'correctness_score': {
                    'type': 'number',
                    'description': 'Score from 0-10 for action correctness',
                    'minimum': 0,
                    'maximum': 10
                },
                'feedback': {
                    'type': 'string',
                    'description': 'Detailed feedback about the action'
                }
            },
            'required': ['action', 'correctness_score', 'feedback']
        }
    }
]

# Decision making toolset for judge
DECISION_MAKING_TOOLS = [
    {
        'name': 'compare_actions',
        'description': 'Compare multiple action candidates and select the best one',
        'parameters': {
            'type': 'object',
            'properties': {
                'action_candidates': {
                    'type': 'array',
                    'items': {
                        'type': 'object',
                        'properties': {
                            'index': {'type': 'integer'},
                            'action': {'type': 'string'},
                            'score': {'type': 'number'}
                        }
                    },
                    'description': 'List of action candidates with scores'
                },
                'selected_index': {
                    'type': 'integer',
                    'description': 'Index of the selected action'
                },
                'reasoning': {
                    'type': 'string',
                    'description': 'Reasoning for the selection'
                }
            },
            'required': ['action_candidates', 'selected_index', 'reasoning']
        }
    }
]

# Toolset registry
TOOLSETS = {
    'browser_automation': BROWSER_AUTOMATION_TOOLS,
    'planning': PLANNING_TOOLS,
    'evaluation': EVALUATION_TOOLS,
    'decision_making': DECISION_MAKING_TOOLS
}


def get_toolset(toolset_name: str):
    """
    Get a predefined toolset by name.
    
    Args:
        toolset_name: Name of the toolset ('browser_automation', 'planning', etc.)
    
    Returns:
        List of tool definitions
    
    Raises:
        KeyError: If toolset name is not found
    """
    if toolset_name not in TOOLSETS:
        raise KeyError(f"Toolset '{toolset_name}' not found. Available: {list(TOOLSETS.keys())}")
    
    return TOOLSETS[toolset_name]


def get_all_tools():
    """Get all available tools from all toolsets."""
    all_tools = []
    for tools in TOOLSETS.values():
        all_tools.extend(tools)
    return all_tools


def get_tool_by_name(tool_name: str):
    """
    Get a specific tool definition by name.
    
    Args:
        tool_name: Name of the tool
    
    Returns:
        Tool definition dict or None if not found
    """
    for tools in TOOLSETS.values():
        for tool in tools:
            if tool['name'] == tool_name:
                return tool
    return None
