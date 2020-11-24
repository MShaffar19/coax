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

from abc import ABC, abstractmethod

import jax
import jax.numpy as jnp
import haiku as hk
import optax
import chex

from .._base.mixins import RandomStateMixin
from ..utils import get_grads_diagnostics, is_policy, is_stochastic, is_qfunction, is_vfunction
from ..value_losses import huber
from ..regularizers import Regularizer


__all__ = (
    'BaseTDLearningV',
    'BaseTDLearningQ',
)


class BaseTDLearning(ABC, RandomStateMixin):
    def __init__(self, f, f_targ=None, optimizer=None, loss_function=None, policy_regularizer=None):

        self._f = f
        self._f_targ = f if f_targ is None else f_targ
        self.loss_function = huber if loss_function is None else loss_function

        if not isinstance(policy_regularizer, (Regularizer, type(None))):
            raise TypeError(
                f"policy_regularizer must be a Regularizer, got: {type(policy_regularizer)}")
        self.policy_regularizer = policy_regularizer

        # optimizer
        self._optimizer = optax.adam(1e-3) if optimizer is None else optimizer
        self._optimizer_state = self.optimizer.init(self._f.params)

        def apply_grads_func(opt, opt_state, params, grads):
            updates, new_opt_state = opt.update(grads, opt_state, params)
            new_params = optax.apply_updates(params, updates)
            return new_opt_state, new_params

        self._apply_grads_func = jax.jit(apply_grads_func, static_argnums=0)

    @abstractmethod
    def target_func(self, target_params, target_state, rng, transition_batch):
        pass

    @property
    @abstractmethod
    def target_params(self):
        pass

    @property
    @abstractmethod
    def target_function_state(self):
        pass

    def update(self, transition_batch, return_td_error=False):
        r"""

        Update the model parameters (weights) of the underlying function approximator.

        Parameters
        ----------
        transition_batch : TransitionBatch

            A batch of transitions.

        return_td_error : bool, optional

            Whether to return the TD-errors.

        Returns
        -------
        metrics : dict of scalar ndarrays

            The structure of the metrics dict is ``{name: score}``.

        td_error : ndarray, optional

            The non-aggregated TD-errors, :code:`shape == (batch_size,)`. This is only returned if
            we set :code:`return_td_error=True`.

        """
        grads, function_state, metrics, td_error = self.grads_and_metrics(transition_batch)
        if any(jnp.any(jnp.isnan(g)) for g in jax.tree_leaves(grads)):
            raise RuntimeError(f"found nan's in grads: {grads}")
        self.update_from_grads(grads, function_state)
        return (metrics, td_error) if return_td_error else metrics

    def update_from_grads(self, grads, function_state):
        r"""

        Update the model parameters (weights) of the underlying function approximator given
        pre-computed gradients.

        This method is useful in situations in which computation of the gradients is deligated to a
        separate (remote) process.

        Parameters
        ----------
        grads : pytree with ndarray leaves

            A batch of gradients, generated by the :attr:`grads` method.

        function_state : pytree

            The internal state of the forward-pass function. See :attr:`Q.function_state
            <coax.Q.function_state>` and :func:`haiku.transform_with_state` for more details.

        """
        self._f.function_state = function_state
        self.optimizer_state, self._f.params = \
            self._apply_grads_func(self.optimizer, self.optimizer_state, self._f.params, grads)

    def grads_and_metrics(self, transition_batch):
        r"""

        Compute the gradients associated with a batch of transitions.

        Parameters
        ----------
        transition_batch : TransitionBatch

            A batch of transitions.

        Returns
        -------
        grads : pytree with ndarray leaves

            A batch of gradients.

        function_state : pytree

            The internal state of the forward-pass function. See :attr:`Q.function_state
            <coax.Q.function_state>` and :func:`haiku.transform_with_state` for more details.

        metrics : dict of scalar ndarrays

            The structure of the metrics dict is ``{name: score}``.

        td_error : ndarray

            The non-aggregated TD-errors, :code:`shape == (batch_size,)`.

        """
        return self._grads_and_metrics_func(
            self._f.params, self.target_params, self._f.function_state, self.target_function_state,
            self._f.rng, transition_batch)

    def td_error(self, transition_batch):
        r"""

        Compute the TD-errors associated with a batch of transitions. We define the TD-error as the
        negative gradient of the :attr:`loss_function` with respect to the predicted value:

        .. math::

            \text{td_error}_i\ =\ -\frac{\partial L(y, \hat{y})}{\partial \hat{y}_i}

        Note that this reduces to the ordinary definition :math:`\text{td_error}=y-\hat{y}` when we
        use the :func:`coax.value_losses.mse` loss funtion.

        Parameters
        ----------
        transition_batch : TransitionBatch

            A batch of transitions.

        Returns
        -------
        td_errors : ndarray, shape: [batch_size]

            A batch of TD-errors.

        """
        return self._td_error_func(
            self._f.params, self.target_params, self._f.function_state, self.target_function_state,
            self._f.rng, transition_batch)

    @property
    def optimizer(self):
        return self._optimizer

    @optimizer.setter
    def optimizer(self, new_optimizer):
        new_optimizer_state_structure = jax.tree_structure(new_optimizer.init(self._f.params))
        if new_optimizer_state_structure != jax.tree_structure(self.optimizer_state):
            raise AttributeError("cannot set optimizer attr: mismatch in optimizer_state structure")
        self._optimizer = new_optimizer

    @property
    def optimizer_state(self):
        return self._optimizer_state

    @optimizer_state.setter
    def optimizer_state(self, new_optimizer_state):
        if jax.tree_structure(new_optimizer_state) != jax.tree_structure(self.optimizer_state):
            raise AttributeError("cannot set optimizer_state attr: mismatch in tree structure")
        self._optimizer_state = new_optimizer_state


class BaseTDLearningV(BaseTDLearning):
    def __init__(self, v, v_targ=None, optimizer=None, loss_function=None, policy_regularizer=None):

        if not is_vfunction(v):
            raise TypeError(f"v must be a v-function, got: {type(v)}")
        if not (v_targ is None or is_vfunction(v_targ)):
            raise TypeError(f"v_targ must be a v-function or None, got: {type(v_targ)}")

        super().__init__(
            f=v,
            f_targ=v_targ,
            optimizer=optimizer,
            loss_function=loss_function,
            policy_regularizer=policy_regularizer)

        def loss_func(params, target_params, state, target_state, rng, transition_batch):
            """

            In this function we tie together all the pieces, which is why it's a bit long.

            The main structure to watch for is calls to self.target_func(...), which is defined
            downstream. All other code is essentially boilerplate to tie this target to the
            predictions, i.e. to construct a feedback signal for training.

            One of the things we might change here is not to handle both the stochastic and
            deterministic cases in the same function.

            -kris

            """
            rngs = hk.PRNGSequence(rng)
            S = self.v.observation_preprocessor(next(rngs), transition_batch.S)
            W = jnp.clip(transition_batch.W, 0.1, 10.)  # clip importance weights to reduce variance

            # regularization term
            if self.policy_regularizer is None:
                regularizer = 0.
            else:
                # flip sign (typical example: regularizer = -beta * entropy)
                regularizer = -self.policy_regularizer.batch_eval(
                    target_params['reg'], target_params['reg_hparams'], target_state['reg'],
                    next(rngs), transition_batch)

            if is_stochastic(self.v):
                dist_params, state_new = self.v.function(params, state, next(rngs), S, True)
                dist_params_target = \
                    self.target_func(target_params, target_state, rng, transition_batch)

                if self.policy_regularizer is not None:
                    dist_params_target = self.v.proba_dist.affine_transform(
                        dist_params_target, 1., regularizer, self.v.value_transform)

                loss = jnp.mean(self.v.proba_dist.cross_entropy(dist_params_target, dist_params))

                # the rest here is only needed for metrics dict
                V = self.v.proba_dist.mean(dist_params)
                V = self.v.proba_dist.postprocess_variate(next(rngs), V, batch_mode=True)
                G = self.v.proba_dist.mean(dist_params_target)
                G = self.v.proba_dist.postprocess_variate(next(rngs), G, batch_mode=True)
                dist_params_v_targ, _ = self.v.function(
                    target_params['v_targ'], target_state['v_targ'], next(rngs), S, False)
                V_targ = self.v.proba_dist.mean(dist_params_v_targ)
                V_targ = self.v.proba_dist.postprocess_variate(next(rngs), V_targ, batch_mode=True)

            else:
                V, state_new = self.v.function(params, state, next(rngs), S, True)
                G = self.target_func(target_params, target_state, next(rngs), transition_batch)
                G += regularizer
                loss = self.loss_function(G, V, W)

                # only needed for metrics dict
                V_targ, _ = self.v.function(
                    target_params['v_targ'], target_state['v_targ'], next(rngs), S, False)

            chex.assert_equal_shape([G, V, V_targ, W])
            chex.assert_rank([G, V, V_targ, W], 1)
            dLoss_dV = jax.grad(self.loss_function, argnums=1)
            td_error = -V.shape[0] * dLoss_dV(G, V)  # e.g. (G - V) if loss function is MSE
            chex.assert_equal_shape([td_error, W])
            metrics = {
                f'{self.__class__.__name__}/loss': loss,
                f'{self.__class__.__name__}/td_error': jnp.mean(W * td_error),
                f'{self.__class__.__name__}/td_error_targ': jnp.mean(-dLoss_dV(V, V_targ, W)),
            }
            return loss, (td_error, state_new, metrics)

        def grads_and_metrics_func(
                params, target_params, state, target_state, rng, transition_batch):

            rngs = hk.PRNGSequence(rng)
            grads, (td_error, state_new, metrics) = jax.grad(loss_func, has_aux=True)(
                params, target_params, state, target_state, next(rngs), transition_batch)

            # add some diagnostics about the gradients
            metrics.update(get_grads_diagnostics(grads, f'{self.__class__.__name__}/grads_'))

            return grads, state_new, metrics, td_error

        def td_error_func(params, target_params, state, target_state, rng, transition_batch):
            loss, (td_error, state_new, metrics) =\
                loss_func(params, target_params, state, target_state, rng, transition_batch)
            return td_error

        self._grads_and_metrics_func = jax.jit(grads_and_metrics_func)
        self._td_error_func = jax.jit(td_error_func)

    @property
    def v(self):
        return self._f

    @property
    def v_targ(self):
        return self._f_targ

    @property
    def target_params(self):
        return hk.data_structures.to_immutable_dict({
            'v': self.v.params,
            'v_targ': self.v_targ.params,
            'reg': getattr(getattr(self.policy_regularizer, 'f', None), 'params', None),
            'reg_hparams': getattr(self.policy_regularizer, 'hyperparams', None)})

    @property
    def target_function_state(self):
        return hk.data_structures.to_immutable_dict({
            'v': self.v.function_state,
            'v_targ': self.v_targ.function_state,
            'reg': getattr(getattr(self.policy_regularizer, 'f', None), 'function_state', None)})

    def _get_target_dist_params(self, params, state, rng, transition_batch):
        r"""

        This method applies techniques from the Distributionel RL paper (arxiv:1707.06887) to
        update StochasticQ / StochasticV.

        """
        rngs = hk.PRNGSequence(rng)
        S_next = self.v_targ.observation_preprocessor(next(rngs), transition_batch.S_next)
        scale, shift = transition_batch.In, transition_batch.Rn  # defines affine transformation
        dist_params_next, _ = self.v_targ.function(params, state, next(rngs), S_next, False)
        dist_params_target = self.v_targ.proba_dist.affine_transform(
            dist_params_next, scale, shift, self.v_targ.value_transform)
        return dist_params_target


class BaseTDLearningQ(BaseTDLearning):
    def __init__(self, q, q_targ=None, optimizer=None, loss_function=None, policy_regularizer=None):

        if not is_qfunction(q):
            raise TypeError(f"q must be a q-function, got: {type(q)}")
        if not (q_targ is None or isinstance(q_targ, (list, tuple)) or is_qfunction(q_targ)):
            raise TypeError(f"q_targ must be a q-function or None, got: {type(q_targ)}")

        super().__init__(
            f=q,
            f_targ=q_targ,
            optimizer=optimizer,
            loss_function=loss_function,
            policy_regularizer=policy_regularizer)

        def loss_func(params, target_params, state, target_state, rng, transition_batch):
            """

            In this function we tie together all the pieces, which is why it's a bit long.

            The main structure to watch for is calls to self.target_func(...), which is defined
            downstream. All other code is essentially boilerplate to tie this target to the
            predictions, i.e. to construct a feedback signal for training.

            One of the things we might change here is not to handle both the stochastic and
            deterministic cases in the same function.

            -kris

            """
            rngs = hk.PRNGSequence(rng)
            S = self.q.observation_preprocessor(next(rngs), transition_batch.S)
            A = self.q.action_preprocessor(next(rngs), transition_batch.A)
            W = jnp.clip(transition_batch.W, 0.1, 10.)  # clip importance weights to reduce variance

            # regularization term
            if self.policy_regularizer is None:
                regularizer = 0.
            else:
                # flip sign (typical example: regularizer = -beta * entropy)
                regularizer = -self.policy_regularizer.batch_eval(
                    target_params['reg'], target_params['reg_hparams'], target_state['reg'],
                    next(rngs), transition_batch)

            if is_stochastic(self.q):
                dist_params, state_new = \
                    self.q.function_type1(params, state, next(rngs), S, A, True)
                dist_params_target = \
                    self.target_func(target_params, target_state, rng, transition_batch)

                if self.policy_regularizer is not None:
                    dist_params_target = self.q.proba_dist.affine_transform(
                        dist_params_target, 1., regularizer, self.q.value_transform)

                loss = jnp.mean(self.q.proba_dist.cross_entropy(dist_params_target, dist_params))

                # the rest here is only needed for metrics dict
                Q = self.q.proba_dist.mean(dist_params)
                Q = self.q.proba_dist.postprocess_variate(next(rngs), Q, batch_mode=True)
                G = self.q.proba_dist.mean(dist_params_target)
                G = self.q.proba_dist.postprocess_variate(next(rngs), G, batch_mode=True)
                dist_params_q_targ, _ = self.q.function_type1(
                    target_params['q_targ'], target_state['q_targ'], next(rngs), S, A, False)
                Q_targ = self.q.proba_dist.mean(dist_params_q_targ)
                Q_targ = self.q.proba_dist.postprocess_variate(next(rngs), Q_targ, batch_mode=True)

            else:
                Q, state_new = self.q.function_type1(params, state, next(rngs), S, A, True)
                G = self.target_func(target_params, target_state, next(rngs), transition_batch)
                G += regularizer
                loss = self.loss_function(G, Q, W)

                # only needed for metrics dict
                Q_targ, _ = self.q.function_type1(
                    target_params['q_targ'], target_state['q_targ'], next(rngs), S, A, False)

            chex.assert_equal_shape([G, Q, Q_targ, W])
            chex.assert_rank([G, Q, Q_targ, W], 1)
            dLoss_dQ = jax.grad(self.loss_function, argnums=1)
            td_error = -Q.shape[0] * dLoss_dQ(G, Q)  # e.g. (G - Q) if loss function is MSE
            chex.assert_equal_shape([td_error, W])
            metrics = {
                f'{self.__class__.__name__}/loss': loss,
                f'{self.__class__.__name__}/td_error': jnp.mean(W * td_error),
                f'{self.__class__.__name__}/td_error_targ': jnp.mean(-dLoss_dQ(Q, Q_targ, W)),
            }
            return loss, (td_error, state_new, metrics)

        def grads_and_metrics_func(
                params, target_params, state, target_state, rng, transition_batch):

            rngs = hk.PRNGSequence(rng)
            grads, (td_error, state_new, metrics) = jax.grad(loss_func, has_aux=True)(
                params, target_params, state, target_state, next(rngs), transition_batch)

            # add some diagnostics about the gradients
            metrics.update(get_grads_diagnostics(grads, f'{self.__class__.__name__}/grads_'))

            return grads, state_new, metrics, td_error

        def td_error_func(params, target_params, state, target_state, rng, transition_batch):
            loss, (td_error, state_new, metrics) =\
                loss_func(params, target_params, state, target_state, rng, transition_batch)
            return td_error

        self._grads_and_metrics_func = jax.jit(grads_and_metrics_func)
        self._td_error_func = jax.jit(td_error_func)

    @property
    def q(self):
        return self._f

    @property
    def q_targ(self):
        return self._f_targ

    @property
    def target_params(self):
        return hk.data_structures.to_immutable_dict({
            'q': self.q.params,
            'q_targ': self.q_targ.params,
            'reg': getattr(getattr(self.policy_regularizer, 'f', None), 'params', None),
            'reg_hparams': getattr(self.policy_regularizer, 'hyperparams', None)})

    @property
    def target_function_state(self):
        return hk.data_structures.to_immutable_dict({
            'q': self.q.function_state,
            'q_targ': self.q_targ.function_state,
            'reg': getattr(getattr(self.policy_regularizer, 'f', None), 'function_state', None)})

    def _get_target_dist_params(self, params, state, rng, transition_batch, A_next):
        r"""

        This method applies techniques from the Distributionel RL paper (arxiv:1707.06887) to
        update StochasticQ / StochasticV.

        """
        rngs = hk.PRNGSequence(rng)
        S_next = self.q_targ.observation_preprocessor(next(rngs), transition_batch.S_next)
        scale, shift = transition_batch.In, transition_batch.Rn  # defines affine transformation
        dist_params_next, _ = self.q_targ.function_type1(
            params, state, next(rngs), S_next, A_next, False)
        dist_params_target = self.q_targ.proba_dist.affine_transform(
            dist_params_next, scale, shift, self.q_targ.value_transform)
        return dist_params_target


class BaseTDLearningQWithTargetPolicy(BaseTDLearningQ):
    def __init__(
            self, q, pi_targ, q_targ=None, optimizer=None,
            loss_function=None, policy_regularizer=None):

        if pi_targ is not None and not is_policy(pi_targ):
            raise TypeError(f"pi_targ must be a Policy, got: {type(pi_targ)}")

        self.pi_targ = pi_targ
        super().__init__(
            q=q,
            q_targ=q_targ,
            optimizer=optimizer,
            loss_function=loss_function,
            policy_regularizer=policy_regularizer)

    @property
    def target_params(self):
        return hk.data_structures.to_immutable_dict({
            'q': self.q.params,
            'q_targ': self.q_targ.params,
            'pi_targ': getattr(self.pi_targ, 'params', None),
            'reg': getattr(getattr(self.policy_regularizer, 'f', None), 'params', None),
            'reg_hparams': getattr(self.policy_regularizer, 'hyperparams', None)})

    @property
    def target_function_state(self):
        return hk.data_structures.to_immutable_dict({
            'q': self.q.function_state,
            'q_targ': self.q_targ.function_state,
            'pi_targ': getattr(self.pi_targ, 'function_state', None),
            'reg':
                getattr(getattr(self.policy_regularizer, 'f', None), 'function_state', None)})
