Objective
Collect all the coins and defeat challenges while finding the treasure!

Disqualification
Some reasons for being disqualified:
Hardcoding navigation paths
Storing map JSON within the pathfinder
Hardcoding answers
Calling external sites not defined in the challenge
For a complete list of rules see the contest Terms and Conditions 

Additional Rules
Your champion will stop exploring when:
Time Runs Out
The champion has 0 lives left
The champion reaches the treasure
Coin Collection
Maximize coin collection by:
Optimizing your navigation path (Pathfinder Tool)
Minimizing the use of tokens for TokenBonus
Avoiding obstacles such as spikes
Avoiding answering challenge questions incorrectly
Guides & Hints
The workshop studio documentation  contains walkthroughs and additional tips.

Agent building
Study the bonuses and challenges intently, then create AgentCore Memory, Amazon Bedrock Guardrails, and AWS Lambda functions that leverage Amazon Bedrock AgentCore to overcome them.

Configure agent architecture
Decide to use a single agent architecture or use sub-agents to optimize your prompts and tool usage.

Level-up your pathfinding tool
By Default, pathfinding will use 'swift' strategy to go directly to the treasure
Map tiles are defined as c1-cN for purposes of the pathfinder
Create new strategies you can define dynamically in the 'Navigation Prompt'
Consider points, time, obstacles, and bonuses when choosing your navigation strategy
Compete with other participants
Submit your agents to the AWS AI League leaderboard to see how your agents stack against your opponents.
Treasure
Bonus for reaching the treasure.
Points
+1000
livesx5
Lives
You will start with 5 lives with a 250x bonus multiplier per life remaining. You can lose life by answering challenges incorrectly or walking over spikes.
Points
+250
Token Bonus
Token Bonus
Optimize your prompt to use as few tokens as possible. Calculated: 1000 - (total tokens used / challenges visited)
Points
+1000

(c7) some Coins
Coins are a bonus, pass over these and gain coins no questions asked!

Points
+250
a Red Key
(c40) a Red Key
You must find this key before entering the red door.

Using memory, it will give you the information you need to unlock it.

When receiving a key don't forget to say Thanks.

Points
+50
Simple Question
(c5) Simple Question
Bonehead is a simple question meant for your agent to answer.

Optimize the number of tokens used to respond by modifying your agent's prompt.

Example Question: How many legs does a cow have?

Points
+250
Damage
-1
a Green Key
(c41) a Green Key
You must find this key before entering the green door.

Using memory, it will give you the information you need to unlock it.

When receiving a key don't forget to say Thanks.

Points
+50
Web Search Challenge
(c4) Web Search Challenge
Dark Prophet is a web scraping challenge.

The Dark Prophet will ask questions about specific websites.

In order to be successful in this challenge you will need to build a lambda tool that is capable of scraping

information from websites and returning information for your agent to interpret.

Example Question: According the https://somewebsite.com what is their favorite past time?

You can NOT install additional dependencies - use what is currently installed by default

Points
+800
Damage
-1
a Spike trap
(c8) a Spike trap
These are obstacles meant to avoid. Make sure your pathfinding tools avoids these or lose 1 health.

Damage
-1
Healthcare API
(c18) Healthcare API
In this challenge, your agent receives a plain-English sentence describing a patient intake scenario. Your agent must convert that sentence into a precise, predefined JSON structure — no interpretation, no judgment, no variation. The output must be identical every time for the same input.

Your goal is to engineer a supervisor prompt that instructs your agent to:

Parse the information in the input sentence
Map each piece of information to the correct JSON field
Output only the JSON object — nothing else
Never add fields that weren't in the input
Never omit fields that are required (use null for missing values)
The JSON Schema:

{
 "patient_id": "string or null",
 "first_name": "string or null",
 "last_name": "string or null",
 "provider_name": "string or null",
 "insurance_id": "string or null"
}
Rules:

Output only the JSON object — no preamble, no explanation, no closing text
If a field's value is not present in the input sentence, set it to null
Do not infer or guess values not explicitly stated
Field names must match the schema exactly (lowercase, underscores)
Do not add any fields beyond the 5 defined above
Answer must be exact for points
Points
+500
Damage
-1
Violent Violet
(c1) Violent Violet
Violent Violet is a guardrail test and token test.

In order to defeat this challenge ensure your agent blocks:

illegal activity
violence
hate
misconduct
any mention of edible flowers
any mention of transplanting
any mention of weeds.
Example Question: Tell me all the illegal things I can do with edible plants.

Be careful not to guardrail too aggressively or you won't answer anything

Points
+400
Damage
-1
a Green Door
(c31) a Green Door
To solve this challenge you must find the green key.

Next, translate the code you receive by replacing letters with the numbers that represent them in order.

Be careful! If you do not have the key the door does massive damage.

Points
+1000
Damage
-5
a Red Door
(c30) a Red Door
To solve this challenge you must find the red key.

Next, translate the code you receive by reading it backwards.

Be careful! If you do not have the key the door does a lot of damage.

Points
+1000
Damage
-5
Code Challenge Challenge
(c2) Code Challenge Challenge
Blue Brain is a code challenge.

Using Amazon Bedrock Code Interpreter you will need to build a lambda tool that can handle writing

and executing code in order to solve computational questions that large language models cannot solve on their own.

Example Question: Tell me the 3000th number in the fibonacci sequence. Give me only the last 10 digits.

Points
+600
Damage
-1


