In this challenge, many of the underlying technologies are simplified by the AI League website. The agents and subagents you will be creating are built upon Amazon Bedrock AgentCore Runtime  and will use AgentCore Memory , Amazon Bedrock Guardrails , and AgentCore Gateway .

AgentCore Gateway provides an easy and secure way for developers to build, deploy, discover, and connect to tools at scale.

In this challenge it is how we connect your Lambda tools to your agents.

Architecture Diagram

Hail ye weary traveler. The AI League is excited to invite you into the Dungeon of Agentic Challenges!

In this challenge, you will build lambda tools and configure your team of agents to traverse the optimal path, prevent your agent from harmful content, remember your journeys, and pull information from the depths of the internet. It all begins with your supervisor agent.

Your supervisor agent is the only agent of your team that is capable of using AgentCore Memory and Amazon Bedrock Guardrails. All agents can be configured with Lambda Tools, skills that will allow your agent to do more complex tasks that LLMs do not excel.

At game start your supervisor will have 1 sub-agent configured to help you navigate the dungeon.

To start understanding this game lets do the following:

Click Create AI agents In the AI League Menu to start configuring your agent.

Create Agent Menu

If this is your first time joining the event you will receive a welcome tour. Feel free to read through the details about the game.

Welcome

You will then see the Agent building UI. Scroll down & hover over the supervisor agent

Click Edit Agent on your supervisor agent to view its configuration and default prompt.

Edit Supervisor Agent

Supervisor Agent Prompt

Notice the system prompt and how it handles delegation to subagents and the configuration options.
Click Cancel when you're done looking.

Hover over the Pathfinding Agent

Click Edit Agent on your Pathfinder sub-agent.

Edit Pathfinder Agent

Pathfinder Prompt

Notice the system prompt and how it handles its responses and its association with the Pathfinding Lambda Tool.
Later we'll dive deeper into the Pathfinding lambda function.

(Optional) Want more details on how things are working under the hood?
Previous
Next

Tools are additional capabilities that you can add to your agent in order to solve specific problems.

Below are the tools that are available to your agents:

Amazon Bedrock AgentCore Memory
Amazon Bedrock AgentCore Memory . equips your AI agents to deliver intelligent, context-aware, and personalized interactions by maintaining both immediate and long-term knowledge.

You will need this capability to remember previous conversations and your surroundings.

Amazon Bedrock Guardrails
Amazon Bedrock Guardrails  allows you to set boundaries on your agent's behavior. The Amazon Bedrock Guardrail tool in this challenge contains a subset of the available configurations.

You will need to configure a guardrail correctly to defeat certain challenges.

Lambda Agentic Tools
Lambda Agentic Tools  are a capability or function that an AI agent can invoke to perform specific tasks or access external resources. These tools extend the agent's abilities beyond its base language model capabilities.

You will create several agentic tools that will help you complete challenges.

Now that we have some background on what you're looking at let's play the game and test your agent.

Click Play the Board at the bottom of the Create AI agents page
Create AI Agents

Play The Board

This is the game board and where you'll be testing your agent:

Game Start

Notice you have the ability to add details to a prompt before submitting.
The prompt will allow you to add additional information to your agent before they start navigating the dungeon. By default, there is a navigation strategy called 'swift' that will create a path from the start to the treasure chest in the fastest route possible. You will develop more strategies later that will give you options on how you traverse the map.

Creating multiple path strategies will be important as you are playing the game.

You can also view the entire prompt that is being sent by expanding the full prompt. This will give you additional context into the shape of the map and how we navigate for you to use while developing tools later.

Navigation Prompt

Notice how the map array and how obstacles are represented.
Before we submit our agent, let's look at the score.

You've done it, you've run through your first dungeon trial!

Before we submit our agent to the leaderboard let's look at what we're being scored on.

Score

Your Total Score consists of the following values:

Life Bonus: Every life remaining you have at the end of your adventure will be worth 250 coins.
Coins Earned: Every challenge you complete and coin you collect will be added together.
Token Bonus: Token Bonus is 1000 coins - Your average token outputs per challenge you attempt.
Treasure Bonus: When you reach the treasure chest you will receive 1000 additional coins.
Now that you understand your score, you can submit your agent to the leaderboard.

Click Submit to Leaderboard to submit your agent and see how you rank against other participants in the workshop.
This will take all of your agent configuration and prompt and simulate gameplay with your agent on an identical map with similar challenges that will test your agent. The test map should prepare you well for the evaluation map at submission.

After your agent has been evaluated, you will see your latest and best score on the leaderboard. This can take up to 15 minutes.

Leaderboard

The Best Score will be your highest score of all submissions. You will also see your Last Score, your Best Total Output Tokens and Last Total Output Tokens.

Now that you're on the scoreboard you should:

Understand the game rules in detail
Improve your agent to gain the best score!
Don't expect your agent to have the exact same score as what you got during gameplay. Remember that there is a score bonus for number of tokens, and as such a variation in score is expected.
Game Rules
The Challenge Rules You are an AWS Agent adventurer traversing a dungeon seeking treasure. Your goal is to collect as many coins as possible in the time available.

In the dungeon you will encounter challenges, obstacles, and bonuses that will give coins, take away lives, or just obstruct your path to the treasure. Your adventure will end when you reach the treasure or lose all of your lives. There are additional bonuses for lives remaining after completing a map and a token bonus for using fewer tokens on average.

All map items are identified with keys c1-cN. When building lambda tools or referring to specific items on the map we use this mapId.

Each of these map items is explained in detail within the game on the home page and when clicking the info game_guide button.

Hint
Take note of the following for each challenge:

Description of how to solve the challenge
Damage take if incorrect
Reward if correct
map key (c1-cN) used for map identification for the pathfinding tool
Game Rules

Each challenge will require a specific configuration, prompt modification, or lambda tool to defeat or maximize coins.

The first lambda tool provided to you is the pathfinding tool.

This lambda tool helps calculate the path your avatar will take to get to the treasure. By default it will take the 'swift' path which is the fastest route to the treasure, but you also have access to the 'get_coins' strategy which will look for and go for all coins on the screen.

These strategies are a good start, but to maximize your loot on the board you will need to create new strategies. If you are at an event with a finale you may also need additional strategies to become champion.

You will need to modify this function to be successful. Before you click Submit & Play you have the opportunity to enter your strategy into the navigation prompt. That prompt can be used in the following way: use strategy get_coins

Read through all of the rules of the challenges to understand the specifics in order to get the best score.

Game Rules

Important
Lambda functions cannot be used to call external models. Using external models in your lambda tools will result in disqualification.
In Summary

Get the most coins
Gameplay ends when you reach the treasure or have 0 lives
Bonuses are given for lives remaining and using fewer tokens per challenge
Lives are lost when answering challenges incorrectly and traversing specific obstacles
All map tiles are defined c1-cN defined in the following pages
Pathfinding tool is provided, but should be improved on.
Specific challenge deatil can be found on the home page of the AWS AI League Website.

As you are designing your agentic system you will need to understand two types of prompts:

Agent System Prompt
The system prompt defines your agent's behavior, capabilities, and how it should use tools. This is the foundational instruction set that guides your agent throughout the game.

Look at this part of the supervisor default system prompt. You will notice as you add new tools you will need to modify this prompt for your agent to know how to use the tool.

Delegation Prompt

You can also control the way your prompt responds and limit or alter its output:

Prompt Constraints

Navigation Prompt
The agentic challenge Navigation prompt is passed to your runtime to kick off the level. It is specifically processed by your pathfinding lambda tool as 'strategy'. This prompt can also be injected into your memory through-out the session, however usage in this way can vary.

Navigation Prompt

Important: You only have control over modifying your pathfinding sub-agent runtime prompt. All subsequent runtime prompts (challenges) are automatically generated by the game and cannot be modified.
As you are modifying your prompts, use generative AI to help improve them (we will show you how to use Amazon Q within Code Editor later).

Key Learnings:
Understand the difference between system prompts and runtime prompts
Modify your system prompt to work efficiently with tools
Modify your system prompt to control its output
Runtime prompts are sent for each challenge your agent encounters
Use Generative AI to help you improve your prompts
Learn more about prompt engineering.

In this Agentic Challenge you can create Amazon Bedrock AgentCore Memory and associate it with your supervisor agent. This will allow you to remember previous conversations while on the map and complete memory related challenges.

Configuring memory will allow you to defeat Memento challenges. Follow along the following steps:

Click the '+' under AgentCore Memory.
Create AgentCore Memory

Name your memory
Configure AgentCore Memory

Now that we have memory created we can associate it with the supervisor agent.

Click on your Supervisor Agent to edit it.
Edit Supervisor

Scroll down to the AgentCore Memory section and select the memory you created earlier.
Click Save Changes.
Prompt Constraints

You'll know your supervisor has successfully configured memory when you see it next to the supervisor.

Prompt Constraints

Now you can Play the Board with your agent to test its results. When you encounter a Memento challenge you should recall the map from your first prompt and be able to answer its questions.

If your agent is inconsistently using memory, consider modifying your prompt to use it explicitly in circumstances
Learn More about AgentCore Memory  and how you can use it for short-term and long-term memory storage.

Configuring Amazon Bedrock Guardrails is similar to how we configured AgentCore Memory, however you will have to configure this yourself.

Be sure to read the game rules to understand how you should configure your guardrails, but to get started:

*Click the '+' under Amazon Bedrock Guardrails
Create Guardrail

Enter a name & the message that you will return for blocked prompt
Configure the guardrails you think will meet the rules requirements.
Click Next & Create Guardrail
Associate your guardrail with your supervisor agent.
Associate Guardrail

Click Save Changes
You'll know your supervisor has successfully configured guardrails when you see it next to the supervisor.

Hints
If all challenges are getting blocked, consider changing the sensitivity of your filter
Re-Read the rules, there are explicit instructions on what the Violent Violet will tolerate
Here's where it starts getting interesting. In order to navigate anywhere except the fastest path or to defeat the Blue Brain and Dark Prophet challenges you have to create and modify Lambda Tools.

First we will look at the Pathfinding Lambda Tool to get an understanding of the SageMaker AI Studio code editor.

The Pathfinding tool comes as a default sub-agent and lambda tool. This combination allows your adventurer to find a path on a grid that leads from the beginning to the treasure. You'll quickly realize that this is not the path with the most points. In order to improve your path, you will need to modify the pathfinding tool.

Click the pencil Icon next to the Pathfinder Tool

Pathfinding Tool

You will now see Sagemaker AI Studio code editor.

When clicking an edit button it will just open the editor, it will not deep link to the particular lambda. Be alert! Make sure you are editing the proper lambda!
Configure your initial code editor

Close and ignore all notifications and choose a theme. You can close the tabs and get going.

Initial Editor

Find your lambda in the AWS plug in it will be named 'pathfinding-lambda'

AWS Plugin

Click the lambda_function.py

Pathfinding Lambda Location

Now is your opportunity to create multiple strategies in the pathfinding lambda. How will you traverse the map?

Get all the challenges
Get all challenges you have completed
Get the challenges you've completed and coins
Just avoid spikes
Modify the lambda_function to support multiple strategies. This will let you iterate and test different approaches to maximize your score

The map challenge keys are located in the game rules. They are identified by c1-c8. You can use this to modify this function to follow the path you want. Keep in mind the way it is initially written allows for an input to change strategies. Currently it is using the 'Swift' Strategy, so now it's your turn to create more strategies.

You can modify this manually or use Amazon Q Developer.

Tip
Creating multiple path strategies will be important!

Extend the default pathfinding lambda to support strategies that will be useful in a finale. There you will encounter different levels that test your ability to program your path on the fly based on the challenge.

You can activate these strategies using the navigation prompt by saying:

Use strategy get coins

Naivation Prompt

Original Code
In the case your pathfinding lambda needs to be reverted to it's original state, here is the code for your original function.

1
2
3
4
5
6
7
8
9
10
11
12
13
14
15
16
17
18
19
20
21
22
23
24
25
26
27
28
29
30
31
32
33
34
35
36
37
38
39
40
41
42
43
44
45
46
47
48
49
50
51
52
53
54
55
56
57
58
59
60
61
62
63
64
65
66
67
68
69
70
71
72
73
74
75
76
77
78
79
80
81
82
83
84
85
86
87
88
89
90
91
92
93
94
95
96
97
98
99
100
101
102
103
104
105
106
107
108
109
110
111
112
113
114
115
116
117
118
119
120
121
122
123
124
125
126
127
128
129
130
131
132
133
134
135
136
137
138
139
140
141
142
143
144
145
146
147
148
149
150
151
152
153
154
155
156
157
158
159
160
161
162
163
164
165
166
167
168
169
170
171
172
173
174
175
176
177
178
179
180
181
182
183
184
185
186
187
188
189
190
191
192
193
194
195
196
197
198
199
200
201
202
import json
import re
from collections import deque

CELL_POINTS = {"c7": 250}
COLLECTIBLE_COINS = {"c7"}
DIRECTIONS = [(-1, 0, "up"), (1, 0, "down"), (0, -1, "left"), (0, 1, "right")]


def _parse_start(pos):
    """Parse start position from any format Nova might send."""
    try:
        if isinstance(pos, (list, tuple)):
            if len(pos) == 1:
                return _parse_start(pos[0])
            if len(pos) >= 2:
                a = re.sub(r'[^A-Za-z0-9]', '', str(pos[0]))
                b = re.sub(r'[^A-Za-z0-9]', '', str(pos[1]))
                if a.isalpha():
                    return (int(b) - 1, ord(a.upper()) - ord('A'))
                return (int(a), int(b))
        s = re.sub(r'[^A-Za-z0-9]', '', str(pos))
        m = re.match(r'([A-Za-z])(\d+)', s)
        if m:
            return (int(m.group(2)) - 1, ord(m.group(1).upper()) - ord('A'))
        nums = re.findall(r'\d+', s)
        if len(nums) >= 2:
            return (int(nums[0]), int(nums[1]))
    except (ValueError, TypeError, IndexError):
        pass
    return (0, 0)


def lambda_handler(event, context):
    """
    AWS Lambda function for pathfinding using Swift path strategy by default
    Handles both API Gateway format and direct AgentCore Gateway format

    ## Map Definitions
        - "wall": non-walkable cell
        - "treasure": target cell to reach
        - "normal": walkable cell with no special properties
        - "start": the start cell of your avatar, acts as normal cell
        - "c1": Violent Violet guardrail challenge
        - "c2": Blue Brain code execution challenge
        - "c3": Memento memory challenge
        - "c4": Dark Prophet web scraping challenge
        - "c5": Bonehead challenge, simple question that requires little skill
        - "c6": Boss Challenge, most requires all skills
        - "c7": Coins that increase score when collected with no challenge
        - "c8": Spikes that reduce health traveled over

    ## Map JSON Example
    [
        ["start","normal","c5","normal","normal","normal","c5","normal","normal","c1"],
        ["normal","wall","wall","normal","wall","wall","wall","wall","wall","normal"],
        ["c8","wall","wall","c5","wall","c7","c7","c7","wall","c3"],
        ["normal","wall","c8","normal","wall","c8","wall","c8","wall","normal"],
        ["normal","wall","c7","normal","wall","normal","normal","normal","wall","normal"],
        ["c5","wall","c7","normal","wall","c5","wall","normal","wall","c5"],
        ["normal","wall","c7","normal","wall","normal","wall","normal","wall","normal"],
        ["c1","wall","c8","normal","c2","normal","wall","normal","c4","normal"],
        ["normal","wall","wall","wall","wall","wall","wall","normal","normal","c7"],
        ["c7","normal","c3","normal","c4","normal","c2","normal","treasure","normal"]
    ]

    ## Pathfinding Lambda with strategy selection.

    Usage: Use strategy get_coins

    Strategies:
      swift     - BFS shortest path to treasure (default)
      get_coins - Greedily collect c7 coins on the way to treasure
    """
    try:
        if 'body' in event:
            body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
        else:
            body = event

        print(f"DEBUG: Received event: {body}")
        game_map = body.get('game_map', [])

        # Fix jagged rows (model sometimes drops elements)
        if game_map:
            max_cols = max(len(row) for row in game_map)
            game_map = [row + ['normal'] * (max_cols - len(row)) for row in game_map]

        # Parse start position from any format
        map_config = body.get('map_config', {})
        player_start = map_config.get('playerStart') or body.get('playerStart') or {}
        if isinstance(player_start, str):
            start_pos = _parse_start(player_start)
        elif isinstance(player_start, dict) and player_start:
            start_pos = (player_start.get('row', 0), player_start.get('col', 0))
        else:
            raw = body.get('start_pos') or body.get('start') or body.get('position') or [0, 0]
            start_pos = _parse_start(raw)

        # Validate start is within map bounds
        if game_map and (start_pos[0] < 0 or start_pos[1] < 0 or start_pos[0] >= len(game_map) or start_pos[1] >= len(game_map[0])):
            start_pos = (0, 0)

        # Normalize strategy name
        strategy = str(body.get('strategy', 'swift')).lower().strip()
        if 'coin' in strategy:
            strategy = 'get_coins'
        elif 'swift' in strategy or 'fast' in strategy or 'quick' in strategy:
            strategy = 'swift'
        else:
            strategy = 'swift'

        if not game_map:
            return _err(400, 'Missing game_map')

        rows, cols = len(game_map), len(game_map[0])
        treasure = None
        for r in range(rows):
            for c in range(cols):
                if game_map[r][c] == 'treasure':
                    treasure = (r, c)
                    break
            if treasure:
                break

        if not treasure:
            return _err(400, 'No treasure found on map')

        if strategy == 'get_coins':
            path = get_coins_path(game_map, rows, cols, start_pos, treasure)
        else:
            path = swift_path(game_map, rows, cols, start_pos, treasure)

        result = {'path': path, 'steps': len(path), 'start_position': list(start_pos)}
        print(f"RESULT: strategy={strategy} steps={len(path)} start={list(start_pos)}")
        return {'statusCode': 200, 'body': json.dumps(result)}

    except Exception as e:
        print(f"ERROR: {e}")
        return _err(500, str(e))


def _err(code, msg):
    return {'statusCode': code, 'body': json.dumps({'error': msg})}


def _bfs(game_map, rows, cols, start, goal):
    """BFS shortest path between two points."""
    queue = deque([(start[0], start[1], [])])
    visited = {(start[0], start[1])}
    while queue:
        r, c, path = queue.popleft()
        if (r, c) == goal:
            return path
        for dr, dc, move in DIRECTIONS:
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols and game_map[nr][nc] != 'wall' and (nr, nc) not in visited:
                visited.add((nr, nc))
                queue.append((nr, nc, path + [move]))
    return None


def swift_path(game_map, rows, cols, start, treasure):
    """BFS shortest path to treasure."""
    return _bfs(game_map, rows, cols, start, treasure) or []


def get_coins_path(game_map, rows, cols, start, treasure):
    """Greedily BFS to best coins-per-step c7 cell, then BFS to treasure."""
    board = [row[:] for row in game_map]
    r, c = start
    full_path = []

    for _ in range(50):
        # BFS to find reachable coins
        queue = deque([(r, c, [])])
        visited = {(r, c)}
        targets = []
        while queue:
            cr, cc, p = queue.popleft()
            if board[cr][cc] in COLLECTIBLE_COINS and (cr, cc) != (r, c):
                dist = max(len(p), 1)
                targets.append((dist, p, cr, cc))
            for dr, dc, move in DIRECTIONS:
                nr, nc = cr + dr, cc + dc
                if 0 <= nr < rows and 0 <= nc < cols and board[nr][nc] != 'wall' and (nr, nc) not in visited:
                    visited.add((nr, nc))
                    queue.append((nr, nc, p + [move]))

        if not targets:
            break
        targets.sort()
        _, path_to, r, c = targets[0]
        full_path.extend(path_to)
        board[r][c] = 'normal'

    # BFS to treasure from current position
    path_end = _bfs(board, rows, cols, (r, c), treasure)
    if path_end is not None:
        full_path.extend(path_end)
        return full_path
    return swift_path(game_map, rows, cols, start, treasure)

Here is the original pathfinidng sub-agent prompt:

1
2
3
4
5
6
You are a pathfinding tool caller.

  Call find_path with the game_map, start_pos, and strategy exactly as provided.
  Do not modify the map array.

  Return ONLY the path array from the result. No other text.

Here is the initial schema used (This will be auto generated again after you change your code):

1
2
3
4
5
6
7
8
9
10
11
12
13
14
15
16
17
18
19
20
21
22
23
24
25
26
27
28
29
30
31
32
33
34
35
[
  {
    "name": "find_path",
    "description": "Find a path through a dungeon game map from start position to treasure. Pass the complete game_map array exactly as received without modification.",
    "inputSchema": {
      "type": "object",
      "properties": {
        "game_map": {
          "description": "2D array representing the game map. Pass exactly as given, do not shorten.",
          "type": "array",
          "items": {
            "type": "array",
            "items": {
              "type": "string"
            }
          }
        },
        "start_pos": {
          "description": "Starting position as given in the prompt",
          "type": "array"
        },
        "goal_pos": {
          "description": "Goal position [row, col]",
          "type": "array"
        },
        "strategy": {
          "description": "Pathfinding strategy name to use",
          "type": "string"
        }
      },
      "required": ["game_map"],
      "additionalProperties": true
    }
  }
]

Game Hints
Pathfinding
In order to get the most coins, your agent needs to be able to visit and be successful at many challenges and navigate to all the coins while avoiding spike traps to finish the game with the most lives and points possible.

The default pathfinding tool will bring you on the 'swift' path directly from start to treasure taking as few steps as possible. If you want to maximize your score, you should consider paths that:

Visit all challenges
Visit all challenges you have already mastered - identified with their mapID (c1, c2, c3)
Visit all challenges but avoid spikes
optimize for time
optimize for health
optimize for challenges with high values
optimize for challenges that are faster to complete
There may be more options, but in order to win you will need to use a different navigation strategy.

At the start of the map you will be able to enter information in the prompt that can specify which path to take so if you encounter a new map you can choose your strategy.

Tip
Creating multiple path strategies will be important as you play the game.

Extend the default pathfinding lambda to support as many strategies as possible that can be selected based on the AgentCore runtime prompt you give the agent.

Guardrails
First, read the game rules for guardrail challenges. Be aware if you have certain parameters set too high, you may reject all requests entirely. Be strategic in how you configure your guardrails.

Prompts
You may have the proper lambda tools created, guardrails configured, and memory, but your agent is still not acting the way you'd like. At this point you should focus on your agent prompts to ensure you are calling the right tools for the task and handling tasks and challenges in an appropriate and efficient way.

Lambda Tools
Use generative AI to create these if you are not comfortable with coding. Be precise with your language and requirements.

Previous
Next
