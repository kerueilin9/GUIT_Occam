"""
ADK Browser Bridge

Converts ADK tool calls to browser environment actions and vice versa.
Provides bidirectional translation between ADK's structured tool interface
and AgentOccam's action string format.
"""

import re
from typing import Dict, List, Any, Optional


class ADKBrowserBridge:
    """
    Bridge between ADK tool calls and browser environment actions.
    
    Handles conversion in both directions:
    - ADK tool calls -> browser action strings
    - Browser action strings -> ADK tool call format (for logging/analysis)
    """
    
    def __init__(self, env=None):
        """
        Initialize the bridge.
        
        Args:
            env: Optional browser environment instance for direct execution
        """
        self.env = env
        self.tool_call_history = []
    
    def execute_tool_call(self, tool_name: str, parameters: Dict[str, Any]) -> Any:
        """
        Convert ADK tool call to browser action and execute it.
        
        Args:
            tool_name: Name of the ADK tool being called
            parameters: Tool parameters
        
        Returns:
            Result from browser environment step
        """
        # Convert to action string
        action_string = self.tool_call_to_action(tool_name, parameters)
        
        # Log the conversion
        self.tool_call_history.append({
            'tool_name': tool_name,
            'parameters': parameters,
            'action_string': action_string
        })
        
        # Execute if environment is available
        if self.env:
            return self.env.step(action_string)
        else:
            return action_string
    
    def tool_call_to_action(self, tool_name: str, parameters: Dict[str, Any]) -> str:
        """
        Convert a single ADK tool call to browser action string.
        
        Args:
            tool_name: Name of the tool
            parameters: Tool parameters
        
        Returns:
            Action string in AgentOccam format
        """
        if tool_name == 'click_element':
            element_id = parameters['element_id']
            return f"click [{element_id}]"
        
        elif tool_name == 'type_text':
            element_id = parameters['element_id']
            text = parameters['text']
            press_enter = parameters.get('press_enter', True)
            enter_flag = 1 if press_enter else 0
            return f"type [{element_id}] [{text}] [{enter_flag}]"
        
        elif tool_name == 'scroll_page':
            direction = parameters['direction']
            return f"scroll [{direction}]"
        
        elif tool_name == 'navigate_to':
            url = parameters['url']
            return f"goto [{url}] [1]"
        
        elif tool_name == 'go_back':
            return "go_back"
        
        elif tool_name == 'stop_task':
            answer = parameters.get('answer', '')
            return f"stop [{answer}]"
        
        elif tool_name == 'branch_plan':
            parent_id = parameters['parent_plan_id']
            subplan = parameters['new_subplan']
            return f"branch [{parent_id}] [{subplan}]"
        
        elif tool_name == 'prune_plan':
            resume_id = parameters['resume_plan_id']
            reason = parameters['reason']
            return f"prune [{resume_id}] [{reason}]"
        
        else:
            raise ValueError(f"Unknown tool: {tool_name}")
    
    def action_to_tool_call(self, action_string: str) -> Optional[Dict[str, Any]]:
        """
        Convert browser action string to ADK tool call format.
        Useful for backward compatibility and logging.
        
        Args:
            action_string: Action string in AgentOccam format
        
        Returns:
            Dict with 'tool_name' and 'parameters' keys, or None if parse fails
        """
        action_string = action_string.strip()
        
        # Parse click action
        match = re.match(r'click\s*\[(\d+)\]', action_string)
        if match:
            return {
                'tool_name': 'click_element',
                'parameters': {'element_id': match.group(1)}
            }
        
        # Parse type action
        match = re.match(r'type\s*\[(\d+)\]\s*\[(.*?)\]\s*\[([01])\]', action_string, re.DOTALL)
        if match:
            return {
                'tool_name': 'type_text',
                'parameters': {
                    'element_id': match.group(1),
                    'text': match.group(2),
                    'press_enter': match.group(3) == '1'
                }
            }
        
        # Parse scroll action
        match = re.match(r'scroll\s*\[(up|down)\]', action_string)
        if match:
            return {
                'tool_name': 'scroll_page',
                'parameters': {'direction': match.group(1)}
            }
        
        # Parse goto action
        match = re.match(r'goto\s*\[(.*?)\]\s*\[1\]', action_string, re.DOTALL)
        if match:
            return {
                'tool_name': 'navigate_to',
                'parameters': {'url': match.group(1)}
            }
        
        # Parse go_back
        if action_string == 'go_back':
            return {
                'tool_name': 'go_back',
                'parameters': {}
            }
        
        # Parse stop action
        match = re.match(r'stop\s*\[(.*?)\]', action_string, re.DOTALL)
        if match:
            return {
                'tool_name': 'stop_task',
                'parameters': {'answer': match.group(1)}
            }
        
        # Parse branch plan
        match = re.match(r'branch\s*\[(\d+)\]\s*\[(.*?)\]', action_string, re.DOTALL)
        if match:
            return {
                'tool_name': 'branch_plan',
                'parameters': {
                    'parent_plan_id': int(match.group(1)),
                    'new_subplan': match.group(2)
                }
            }
        
        # Parse prune plan
        match = re.match(r'prune\s*\[(\d+)\]\s*\[(.*?)\]', action_string, re.DOTALL)
        if match:
            return {
                'tool_name': 'prune_plan',
                'parameters': {
                    'resume_plan_id': int(match.group(1)),
                    'reason': match.group(2)
                }
            }
        
        # Unknown action format
        return None
    
    def batch_convert_actions(self, action_list: List[str]) -> List[str]:
        """
        Convert a list of action strings to browser-executable format.
        
        Args:
            action_list: List of action strings (may be tool calls or direct actions)
        
        Returns:
            List of browser-executable action strings
        """
        result = []
        for action in action_list:
            # Try to parse as tool call format
            tool_call = self.action_to_tool_call(action)
            if tool_call:
                # Already in correct format
                result.append(action)
            else:
                # Keep as-is (might be a note or other action)
                result.append(action)
        
        return result
    
    def get_tool_call_summary(self) -> str:
        """
        Get a summary of all tool calls executed through this bridge.
        
        Returns:
            Formatted string summarizing tool calls
        """
        if not self.tool_call_history:
            return "No tool calls executed yet"
        
        summary = [f"Total tool calls: {len(self.tool_call_history)}\\n"]
        
        # Count by tool type
        tool_counts = {}
        for call in self.tool_call_history:
            tool_name = call['tool_name']
            tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
        
        summary.append("Tool usage breakdown:")
        for tool_name, count in sorted(tool_counts.items(), key=lambda x: -x[1]):
            summary.append(f"  - {tool_name}: {count} times")
        
        return "\\n".join(summary)
    
    def clear_history(self):
        """Clear tool call history."""
        self.tool_call_history = []


# Convenience functions for direct use

def convert_tool_call(tool_name: str, parameters: Dict[str, Any]) -> str:
    """
    Quick conversion of tool call to action string without bridge instance.
    
    Args:
        tool_name: Name of the ADK tool
        parameters: Tool parameters
    
    Returns:
        Action string
    """
    bridge = ADKBrowserBridge()
    return bridge.tool_call_to_action(tool_name, parameters)


def parse_action(action_string: str) -> Optional[Dict[str, Any]]:
    """
    Quick parsing of action string to tool call format without bridge instance.
    
    Args:
        action_string: Action string to parse
    
    Returns:
        Tool call dict or None
    """
    bridge = ADKBrowserBridge()
    return bridge.action_to_tool_call(action_string)
