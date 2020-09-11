# ------------------------------------------------------------------------------------------------ #
# MIT License                                                                                      #
#                                                                                                  #
# Copyright (c) 2020, Microsoft Corporation                                                        #
#                                                                                                  #
# Permission is hereby granted, free of charge, to any person obtaining a copy of this software    #
# and associated documentation files (the "Software"), to deal in the Software without             #
# restriction, including without limitation the rights to use, copy, modify, merge, publish,       #
# distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the    #
# Software is furnished to do so, subject to the following conditions:                             #
#                                                                                                  #
# The above copyright notice and this permission notice shall be included in all copies or         #
# substantial portions of the Software.                                                            #
#                                                                                                  #
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING    #
# BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND       #
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,     #
# DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,   #
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.          #
# ------------------------------------------------------------------------------------------------ #

from ..proba_dists import ProbaDist
from .base_model import BaseModel


__all__ = (
    'DynamicsModel',
)


class DynamicsModel(BaseModel):
    r"""

    A parametrized dynamics model :math:`p_\theta(s'|s,a)`. Here, :math:`s'` is the successor state,
    given that we take action :math:`a` from state :math:`s`.

    Parameters
    ----------
    func : function

        A Haiku-style function that specifies the forward pass.

    env : gym.Env

        The gym-style environment. This is used to validate the input/output structure of ``func``.

    action_preprocessor : function, optional

        Turns a single action into a batch of actions that are compatible with the corresponding
        probability distribution. If left unspecified, this defaults to:

        .. code:: python

            action_preprocessor = ProbaDist(action_space).preprocess_variate

        See also :attr:`coax.proba_dists.ProbaDist.preprocess_variate`.

    proba_dist : ProbaDist, optional

        A probability distribution that is used to interpret the output of :code:`func
        <coax.Policy.func>`. Check out the :mod:`coax.proba_dists` module for available options.

        If left unspecified, this defaults to:

        .. code:: python

            proba_dist = coax.proba_dists.ProbaDist(observation_space)

    random_seed : int, optional

        Seed for pseudo-random number generators.

    """
    def __init__(self, func, env, action_preprocessor=None, proba_dist=None, random_seed=None):
        if action_preprocessor is None:
            action_preprocessor = ProbaDist(env.action_space).preprocess_variate
        if proba_dist is None:
            proba_dist = ProbaDist(env.observation_space)
        observation_preprocessor = proba_dist.preprocess_variate

        super().__init__(
            func=func,
            observation_space=env.observation_space,
            action_space=env.action_space,
            observation_preprocessor=observation_preprocessor,
            action_preprocessor=action_preprocessor,
            proba_dist=proba_dist,
            random_seed=random_seed)

    @classmethod
    def example_data(
            cls, observation_space, action_space,
            action_preprocessor=None, proba_dist=None, batch_size=1, random_seed=None):

        if action_preprocessor is None:
            action_preprocessor = ProbaDist(action_space).preprocess_variate
        if proba_dist is None:
            proba_dist = ProbaDist(observation_space)
        observation_preprocessor = proba_dist.preprocess_variate

        return super().example_data(
            observation_space=observation_space,
            action_space=action_space,
            observation_preprocessor=observation_preprocessor,
            action_preprocessor=action_preprocessor,
            proba_dist=proba_dist,
            batch_size=batch_size,
            random_seed=random_seed)

    def __call__(self, s, a=None, return_logp=False):
        r"""

        Sample a successor state :math:`s'` from the dynamics model :math:`p(s'|s,a)`.

        Parameters
        ----------
        s : state observation

            A single state observation :math:`s`.

        a : action, optional

            A single action :math:`a`. This is *required* if the actions space is non-discrete.

        return_logp : bool, optional

            Whether to return the log-propensity :math:`\log p(s'|s,a)`.

        Returns
        -------
        s_next : state observation or list thereof

            Depending on whether :code:`a` is provided, this either returns a single next-state
            :math:`s'` or a list of :math:`n` next-states, one for each discrete action.

        logp : non-positive float or list thereof, optional

            The log-propensity :math:`\log p(s'|s,a)`. This is only returned if we set
            ``return_logp=True``. Depending on whether :code:`a` is provided, this is either a
            single float or a list of :math:`n` floats, one for each discrete action.

        """
        return super().__call__(s, a=a, return_logp=return_logp)

    def mode(self, s, a=None):
        r"""

        Get the most probable successor state :math:`s'` according to the dynamics model,
        :math:`s'=\arg\max_{s'}p_\theta(s'|s,a)`.

        Parameters
        ----------
        s : state observation

            A single state observation :math:`s`.

        a : action, optional

            A single action :math:`a`. This is *required* if the actions space is non-discrete.

        Returns
        -------
        s_next : state observation or list thereof

            Depending on whether :code:`a` is provided, this either returns a single next-state
            :math:`s'` or a list of :math:`n` next-states, one for each discrete action.

        """
        return super().mode(s, a=a)

    def dist_params(self, s, a=None):
        r"""

        Get the parameters of the conditional probability distribution :math:`p_\theta(s'|s,a)`.

        Parameters
        ----------
        s : state observation

            A single state observation :math:`s`.

        a : action, optional

            A single action :math:`a`. This is *required* if the actions space is non-discrete.

        Returns
        -------
        dist_params : dict or list of dicts

            Depending on whether :code:`a` is provided, this either returns a single dist-params
            dict or a list of :math:`n` such dicts, one for each discrete action.

        """
        return super().dist_params(s, a=a)
