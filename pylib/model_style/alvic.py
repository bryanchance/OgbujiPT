# SPDX-FileCopyrightText: 2023-present Uche Ogbuji <uche@ogbuji.net>
#
# SPDX-License-Identifier: Apache-2.0
# ogbujipt.model_styles.alvic

'''
Model style for Alpaca (instruction based) or Vicuña (Q&A).

Plain Alpaca style, e.g.:

* WizardLM

Alpaca-instruct style, e.g.

* Nous-Hermes

Vicuña style, e.g.

* Robin

Useful collection of Alpaca demo prompts: https://huggingface.co/datasets/tatsu-lab/alpaca
'''

from enum import Enum

from functools import partial


class sub_style(Enum):
    ALPACA = 1
    ALPACA_INSTRUCT = 2
    VICUNA = 3


# ALternatives, for convenience
ALPACA = sub_style.ALPACA
VICUNA = sub_style.VICUNA
ALPACA_INSTRUCT = sub_style.ALPACA_INSTRUCT


# XXX Try out preambles to instructions, e.f. for jailbreaks?

ALPACA_PROMPT_TMPL = '''\
{instru_marker}{instru_inputs}

### Response:
'''


VICUNA_PROMPT_TMPL = '''\
### USER:

{query}

### ASSISTANT:
'''


def make_prompt(msg, inputs='', sub=sub_style.ALPACA):
    '''

    '''
    if sub in (sub_style.ALPACA, sub_style.ALPACA_INSTRUCT):
        # Roundabout method needed pre Python 3.12 because of escaping limitations
        cr = '\n'
        instru_inputs = f'{msg}\n{"### Inputs:" + cr + inputs if inputs else "" }\n'
        instru_marker = '### Instruction:\n\n' if sub == sub_style.ALPACA_INSTRUCT else ''
        return ALPACA_PROMPT_TMPL.format(
            instru_marker=instru_marker,
            instru_inputs=instru_inputs)
    elif sub == sub_style.VICUNA:
        return VICUNA_PROMPT_TMPL.format(query=msg)
    else:
        raise ValueError('Prompt substyle should be Alpaca or Vicuna, not ', sub)