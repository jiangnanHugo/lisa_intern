import numpy as np
from theano import tensor as T
from theano import config
from theano.printing import Print
from theano.sandbox.rng_mrg import MRG_RandomStreams
from theano.tensor.shared_randomstreams import RandomStreams
from theano.compat.python2x import OrderedDict
from pylearn2.models.mlp import MLP
from pylearn2.models.mlp import Softmax
from pylearn2.monitor import get_monitor_doc
from pylearn2.space import VectorSpace, IndexSpace
from pylearn2.format.target_format import OneHotFormatter
#from theano.sandbox.rng_mrg import MRG_RandomStreams
from pylearn2.utils import sharedX
from pylearn2.sandbox.nlp.linear.matrixmul import MatrixMul
from pylearn2.models.model import Model
from pylearn2.sandbox.nlp.models.lblcost import Default
#import ipdb
from pylearn2.models import mlp
from pylearn2.models.mlp import Layer
from pylearn2.space import CompositeSpace
from pylearn2.costs.cost import Cost
import math
import warnings
import theano
from pylearn2.utils import py_integer_types
from itertools import izip
from pylearn2.utils import as_floatX
class vLBLSoft(Model):
    def __init__(self, dict_size, dim, context_length, k, irange = 0.1, seed = 22):
        super(vLBLSoft, self).__init__()
        rng = np.random.RandomState(seed)
        self.rng = rng
        self.context_length = context_length
        self.dim = dim
        self.dict_size = dict_size
        C_values = np.asarray(rng.normal(0, math.sqrt(irange),
                                         size=(dim,context_length)),
                              dtype=theano.config.floatX)
        self.C = theano.shared(value=C_values, name='C', borrow=True)
        W_context = rng.uniform(-irange, irange, (dict_size, dim))
        W_context = sharedX(W_context,name='W_context')
        W_target = rng.uniform(-irange, irange, (dict_size, dim))
        W_target = sharedX(W_target,name='W_target')
        self.projector_context = MatrixMul(W_context)
        self.projector_target = MatrixMul(W_target)
        self.W_context = W_context
        self.W_target = W_target
        self.W_target = W_context
        b_values = np.asarray(rng.normal(0, math.sqrt(irange), size=(dict_size,)),
                              dtype=theano.config.floatX)
        self.b = theano.shared(value=b_values, name='b', borrow=True)
        self.input_space = IndexSpace(dim = context_length, max_labels = dict_size)
        self.output_space = IndexSpace(dim = 1, max_labels = dict_size)
        self.allY = T.as_tensor_variable(np.arange(dict_size,dtype=np.int64).reshape(dict_size,1))
    
    def get_params(self):
        #get W from projector
        rval1 = self.projector_context.get_params()
        rval2 = self.projector_target.get_params()
                #add C, b
        rval1.extend([self.C, self.b])
        rval1.extend(rval2)
        return rval1
    
    def fprop(self, state_below):
        """
        state_below is r_w?
        """
        state_below = state_below.reshape((state_below.shape[0], self.dim, self.context_length))
        rval = self.C.dimshuffle('x', 0, 1) * state_below
        rval = rval.sum(axis=2)
        return rval
    
    def get_default_cost(self):
        return Default()
    
    def get_monitoring_data_specs(self):
        """
        Returns data specs requiring both inputs and targets."""
        space = CompositeSpace((self.get_input_space(),
                                self.get_output_space()))
        source = (self.get_input_source(), self.get_target_source())
        return (space, source)
    
    def get_monitoring_channels(self, data):
        rval = OrderedDict()
        rval['nll'] = self.cost_from_X(data)
        rval['perplexity'] = 10 ** (rval['nll']/np.log(10).astype('float32'))
        return rval
    
    def score(self, all_q_w, q_h):
        sallwh = T.dot(q_h,all_q_w.T) + self.b.dimshuffle('x',0)
        return sallwh

    def cost_from_X(self, data):
        X, Y = data
        X = self.projector_context.project(X)
        q_h = self.fprop(X)
        rval = self.cost(Y,q_h)
        return rval
    
    def cost(self,Y,q_h):
        all_q_w = self.W_target
        s = self.score(all_q_w,q_h)
        p_w_given_h = T.nnet.softmax(s)
        return T.cast(-T.mean(T.diag(T.log(p_w_given_h)[T.arange(Y.shape[0]), Y])),theano.config.floatX)

    def apply_dropout(self, state, include_prob, scale, theano_rng, input_space, mask_value=0, per_example=True):
        """
        per_example : bool, optional
            Sample a different mask value for every example in a batch.
            Defaults to `True`. If `False`, sample one mask per mini-batch.
        """
        if include_prob in [None, 1.0, 1]:
            return state
        assert scale is not None
        if isinstance(state, tuple):
            return tuple(self.apply_dropout(substate, include_prob,
                                            scale, theano_rng, mask_value)
                         for substate in state)
        if per_example:
            mask = theano_rng.binomial(p=include_prob, size=state.shape,
                                       dtype=state.dtype)
        else:
            batch = input_space.get_origin_batch(1)
            mask = theano_rng.binomial(p=include_prob, size=batch.shape,
                                       dtype=state.dtype)
            rebroadcast = T.Rebroadcast(*zip(xrange(batch.ndim),
                                             [s == 1 for s in batch.shape]))
            mask = rebroadcast(mask)
        if mask_value == 0:
            rval = state * mask * scale
        else:
            rval = T.switch(mask, state * scale, mask_value)
        return T.cast(rval, state.dtype)


    def dropout_fprop(self, state_below, default_input_include_prob=0.5,
                      input_include_probs=None, default_input_scale=2.,
                      input_scales=None, per_example=True):

        if input_include_probs is None:
            input_include_probs = {}

        if input_scales is None:
            input_scales = {}

        theano_rng = MRG_RandomStreams(max(self.rng.randint(2 ** 15), 1))

        include_prob = default_input_include_prob
        scale = default_input_scale
        state_below = self.apply_dropout(
                state=state_below,
                include_prob=include_prob,
                theano_rng=theano_rng,
                scale=scale,
                #check
                mask_value=0,
                input_space=self.get_input_space(),
                per_example=per_example
            )
        state_below = self.fprop(state_below)

        return state_below

class vLBL(Model):

    def __init__(self, dict_size, dim, context_length, k, irange = 0.1, seed = 22,  max_row_norm=None,max_col_norm=None):

        rng = np.random.RandomState(seed)
        self.rng = rng
        self.k = k
        self.context_length = context_length
        self.dim = dim
        self.dict_size = dict_size
        C = rng.randn(dim, context_length)
        self.C = sharedX(C)

        W = rng.uniform(-irange, irange, (dict_size, dim))
        W = sharedX(W)
        self.W=W
        # TODO maybe have another projector for tagets
        self.projector = MatrixMul(W)

        self.b = sharedX(np.zeros((dict_size,)), name = 'vLBL_b')

        self.input_space = IndexSpace(dim = context_length, max_labels = dict_size)
        #self.output_space = IndexSpace(dim = 1, max_labels = dict_size)
        self.output_space = VectorSpace(dim = dict_size)
        self.max_row_norm=max_row_norm
        self.max_col_norm=max_col_norm
    def get_params(self):

        rval = self.projector.get_params()
        rval.extend([self.C, self.b])
        return rval

    def get_default_cost(self):
        return Default()


    def fprop(self, state_below):
        "q^(h) from EQ. 2"

        state_below = state_below.reshape((state_below.shape[0], self.dim, self.context_length))
        rval = self.C.dimshuffle('x', 0, 1) * state_below
        rval = rval.sum(axis=2)
        return rval

    def _modify_updates(self,updates):
        #for param in self.get_params():
        if self.max_row_norm is not None:
            W = self.W
            if W in updates:
                updated_W = updates[W]
                row_norms = T.sqrt(T.sum(T.sqr(updated_W), axis=1))
                desired_norms = T.clip(row_norms, 0, self.max_row_norm)
                scales = desired_norms / (1e-7 + row_norms)
                updates[W] = updated_W * scales.dimshuffle(0, 'x')
        if self.max_col_norm is not None:
            assert self.max_row_norm is None
            W = self.W
            if W in updates:
                updated_W = updates[W]
                col_norms = T.sqrt(T.sum(T.sqr(updated_W), axis=0))
                desired_norms = T.clip(col_norms, 0, self.max_col_norm)
                updates[W] = updated_W * (desired_norms / (1e-7 + col_norms))

    def score(self,q_h):
        q_w = self.projector._W
        rval = T.dot(q_h, q_w.T) + self.b.dimshuffle('x', 0)
        return rval

    def cost(self,Y,q_h):
        z = self.score(q_h)
        z = z - z.max(axis=1).dimshuffle(0, 'x')
        log_prob = z - T.log(T.exp(z).sum(axis=1).dimshuffle(0, 'x'))
        log_prob_of = (Y * log_prob).sum(axis=1)
        assert log_prob_of.ndim == 1
        rval = as_floatX(log_prob_of.mean())
        return - rval

    def cost_from_X(self, data):
        X, Y = data
        X = self.projector.project(X)
        q_h = self.fprop(X)
        return self.cost(Y,q_h)


    def get_monitoring_data_specs(self):

        space = CompositeSpace((self.get_input_space(),
                                self.get_output_space()))
        source = (self.get_input_source(), self.get_target_source())
        return (space, source)

    def get_monitoring_channels(self, data):
        X, Y = data
        rval = OrderedDict()
        
        W_context = self.W
        W_target = self.W
        b = self.b
        C = self.C

        sq_W_context = T.sqr(W_context)
        # sq_W_target = T.sqr(W_target)
        sq_b = T.sqr(b)
        sq_c = T.sqr(C)

        row_norms_W_context = T.sqrt(sq_W_context.sum(axis=1))
        col_norms_W_context = T.sqrt(sq_W_context.sum(axis=0))

        # row_norms_W_target = T.sqrt(sq_W_target.sum(axis=1))
        # col_norms_W_target = T.sqrt(sq_W_target.sum(axis=0))
        
        col_norms_b = T.sqrt(sq_b.sum(axis=0))

        
        col_norms_c = T.sqrt(sq_c.sum(axis=0))

        rval = OrderedDict([
                            ('W_context_row_norms_min'  , row_norms_W_context.min()),
                            ('W_context_row_norms_mean' , row_norms_W_context.mean()),
                            ('W_context_row_norms_max'  , row_norms_W_context.max()),
                            ('W_context_col_norms_min'  , col_norms_W_context.min()),
                            ('W_context_col_norms_mean' , col_norms_W_context.mean()),
                            ('W_context_col_norms_max'  , col_norms_W_context.max()),

                            # ('W_target_row_norms_min'  , row_norms_W_target.min()),
                            # ('W_target_row_norms_mean' , row_norms_W_target.mean()),
                            # ('W_target_row_norms_max'  , row_norms_W_target.max()),
                            # ('W_target_col_norms_min'  , col_norms_W_target.min()),
                            # ('W_target_col_norms_mean' , col_norms_W_target.mean()),
                            # ('W_target_col_norms_max'  , col_norms_W_target.max()),
                            
                            ('b_col_norms_min'  , col_norms_b.min()),
                            ('b_col_norms_mean' , col_norms_b.mean()),
                            ('b_col_norms_max'  , col_norms_b.max()),

                            ('c_col_norms_min'  , col_norms_c.min()),
                            ('c_col_norms_mean' , col_norms_c.mean()),
                            ('c_col_norms_max'  , col_norms_c.max()),
                            ])
            
        nll = self.cost_from_X(data)
        
        rval['perplexity'] = as_floatX(10 ** (nll/np.log(10)))
        return rval


    def apply_dropout(self, state, include_prob, scale, theano_rng, input_space, mask_value=0, per_example=True):
        """
        per_example : bool, optional
            Sample a different mask value for every example in a batch.
            Defaults to `True`. If `False`, sample one mask per mini-batch.
        """
        if include_prob in [None, 1.0, 1]:
            return state
        assert scale is not None
        if isinstance(state, tuple):
            return tuple(self.apply_dropout(substate, include_prob,
                                            scale, theano_rng, mask_value)
                         for substate in state)
        if per_example:
            mask = theano_rng.binomial(p=include_prob, size=state.shape,
                                       dtype=state.dtype)
        else:
            batch = input_space.get_origin_batch(1)
            mask = theano_rng.binomial(p=include_prob, size=batch.shape,
                                       dtype=state.dtype)
            rebroadcast = T.Rebroadcast(*zip(xrange(batch.ndim),
                                             [s == 1 for s in batch.shape]))
            mask = rebroadcast(mask)
        if mask_value == 0:
            rval = state * mask * scale
        else:
            rval = T.switch(mask, state * scale, mask_value)
        return T.cast(rval, state.dtype)


    def dropout_fprop(self, state_below, default_input_include_prob=0.5,
                      input_include_probs=None, default_input_scale=2.,
                      input_scales=None, per_example=True):

        if input_include_probs is None:
            input_include_probs = {}

        if input_scales is None:
            input_scales = {}

        theano_rng = MRG_RandomStreams(max(self.rng.randint(2 ** 15), 1))

        include_prob = default_input_include_prob
        scale = default_input_scale
        state_below = self.apply_dropout(
                state=state_below,
                include_prob=include_prob,
                theano_rng=theano_rng,
                scale=scale,
                #check
                mask_value=0,
                input_space=self.get_input_space(),
                per_example=per_example
            )
        state_below = self.fprop(state_below)

        return state_below

class vLBLNCE(vLBLSoft):
    
    def __init__(self, dict_size, dim, context_length, k, irange = 0.1, seed = 22):
        super(vLBLNCE, self).__init__(dict_size, dim, context_length,k)
        self.k = k

    def score(self, X, Y, noisegiven=False):
        
        X = self.projector_context.project(X)
        q_h = self.fprop(X)
            
        if noisegiven == False:
            q_w = self.projector_target.project(Y)
            swh = (q_w*q_h).sum(axis=1) + self.b[Y].flatten()
        else:
            q_w = self.projector_target.project(Y)
            q_h = q_h.dimshuffle(0,'x',1)
            #shape is number of examples x noise_per_clean x dimensions
            swh = (q_w*q_h).sum(axis=2) + self.b[Y]
        return swh
        #shape is numExamples x noise+per+clean

        #return self.score(X, Y, ndim = ndim) - T.log(self.k * p_n)
    def prob_data_given_word_theta(self,delta_rval):
        return T.nnet.sigmoid(delta_rval)

    def get_monitoring_channels(self, data):
        rval = OrderedDict()
        rval['nll'] = self.cost_from_X(data)
        rval['perplexity'] = 10 ** (rval['nll']/np.log(10).astype('float32'))
        return rval
    def delta(self, data,noise=None):
        X, Y = data
        if noise is None:
            p_n = 1. / self.dict_size
            de = self.score(X,Y)
            de = de - T.log(self.k*p_n)
            return de
        else:
            p_n = 1. / self.dict_size
            s = self.score(X,noise,True)
            #this de is 15x3. score for each of the noise sample
            de = s - T.log(self.k*p_n)
            return s,de
        #this is only for uniform(?)

    def cost_from_X(self,data):
        delta_rval = self.delta(data)
        prob = self.prob_data_given_word_theta(delta_rval)
        logprob = T.log(prob)
        logprobnoise = T.log(1-prob) 
        return -(T.mean(logprob)+T.mean(logprobnoise))
        #return -T.mean(delta_rv)
        #expectation over data of log prob_data_given_word_theta
        # + k* (expectation over noise distribution)[1 - prob_data_given_word_theta]

class CostNCE(Cost):
    def __init__(self,samples):
        assert isinstance (samples, py_integer_types)
        self.noise_per_clean = samples
        self.random_stream = RandomStreams(seed=1)

    def expr(self,model,data):
        return None

    def get_data_specs(self, model):
        space = CompositeSpace((model.get_input_space(),
                                model.get_output_space()))
        source = (model.get_input_source(), model.get_target_source())
        return (space, source)
    def get_noise(self,Y):
        theano_rng = RandomStreams(0)
        noise = theano_rng.uniform(size=(Y.shape[0]*self.noise_per_clean,1) , low = 0, high = 10000, dtype='int32')
        return sharedX(noise)
        
    def get_gradients(self, model, data, **kwargs):
        params = model.get_params()
        X,Y=data
	#noise = self.get_noise(Y)
        dtynoise = np.ones((100,15),dtype='int32')
        #noise = noise.reshape((Y.shape[0],self.noise_per_clean))
        #this is 15x3
        #both of below are 15x3
        score_noise,delta_noise = model.delta(data,dtynoise)
        

        delta_y = model.delta(data)
        prob = T.nnet.sigmoid(delta_y)
        phw = T.exp(model.score(X,Y))
        first_part =  [(1-prob)* item for item in theano.gradient.jacobian(phw,params)]
        #print T.nnet.sigmoid(delta_noise.flatten()).type
        #print theano.gradient.jacobian(score_noise.flatten(),params)[0].type

        #delta_noise adn score_noise are also 100x15
        to_sum = [T.nnet.sigmoid(delta_noise.flatten()) * item for item in theano.gradient.jacobian(score_noise.flatten(),params)]
        to_sum = [item.reshape(dtynoise.shape) for item in to_sum]
        noise_part = [T.mean(item,axis=1)  for item in to_sum]

        grads = [x-y for x in first_part for y in noise_part if first_part.index(x)==noise_part.index(y)]
        print grads[0].type
        print type(grads[0])
        print grads[1].type
        print type(grads[1])
        print grads[2].type
        print type(grads[2])
        gradients = OrderedDict(izip(params, grads))
        updates = OrderedDict()
        return gradients, updates






# class vLBL(Model):
    
#     def score(self, X, Y):
#         X = self.projector_context.project(X)
#         q_h = self.fprop(X)
#         q_w = self.projector_target.project(Y)
#         all_q_w = self.projector_target.project(self.allY)
#         swh = (q_w*q_h).sum(axis=1) + self.b[Y].flatten()
#         sallwh = T.dot(q_h,all_q_w.T) + self.b.dimshuffle('x',0)
#         swh = T.exp(swh)
#         sallwh = T.exp(sallwh).sum(axis=1)
#         return swh, sallwh

#     def cost_from_X(self, data):
#         X, Y = data
#         s,sallwh = self.score(X,Y)
#         prob = s/sallwh
#         return -T.mean(T.log(prob))
    
#     def __init__(self, dict_size, dim, context_length, k, irange = 0.1, seed = 22):
#   super(vLBL, self).__init__()
#         rng = np.random.RandomState(seed)
#         self.rng = rng
#         self.context_length = context_length
#         self.dim = dim
#         self.dict_size = dict_size

#         C_values = np.asarray(rng.normal(0, math.sqrt(irange),
#                                          size=(dim,context_length)),
#                               dtype=theano.config.floatX)
#         self.C = theano.shared(value=C_values, name='C', borrow=True)

#         W_context = rng.uniform(-irange, irange, (dict_size, dim))
#         W_context = sharedX(W_context,name='W_context')
#         W_target = rng.uniform(-irange, irange, (dict_size, dim))
#         W_target = sharedX(W_target,name='W_target')
#         self.projector_context = MatrixMul(W_context)
#         self.projector_target = MatrixMul(W_target)
        
#         self.W_context = W_context
#         self.W_target = W_target

#         b_values = np.asarray(rng.normal(0, math.sqrt(irange), size=(dict_size,)),
#                               dtype=theano.config.floatX)
#         self.b = theano.shared(value=b_values, name='b', borrow=True)

#         self.input_space = IndexSpace(dim = context_length, max_labels = dict_size)
#         self.output_space = IndexSpace(dim = 1, max_labels = dict_size)

#         self.allY = T.as_tensor_variable(np.arange(dict_size,dtype=np.int64).reshape(dict_size,1))
   
#     def get_params(self):
#         #get W from projector
#         rval1 = self.projector_context.get_params()
#         rval2 = self.projector_target.get_params()
#                 #add C, b
#         rval1.extend([self.C, self.b])
#         rval1.extend(rval2)
#         return rval1

#     def fprop(self, state_below):
#         """
#         state_below is r_w?
#         """
#         state_below = state_below.reshape((state_below.shape[0], self.dim, self.context_length))
#         rval = self.C.dimshuffle('x', 0, 1) * state_below
#         rval = rval.sum(axis=2)
#         return rval

#     def get_default_cost(self):
#         return Default()

#     def get_monitoring_data_specs(self):
#         """
#         Returns data specs requiring both inputs and targets.

#         Returns
#         -------
#         data_specs: TODO
#             The data specifications for both inputs and targets.
#         """
#         space = CompositeSpace((self.get_input_space(),
#                                 self.get_output_space()))
#         source = (self.get_input_source(), self.get_target_source())
#         return (space, source)

#     def get_monitoring_channels(self, data):

#         # W_context = self.W_context
#         # W_target = self.W_target
#         # b = self.b
#         # C = self.C

#         # sq_W_context = T.sqr(W_context)
#         # sq_W_target = T.sqr(W_target)
#         # sq_b = T.sqr(b)
#         # sq_c = T.sqr(C)

#         # row_norms_W_context = T.sqrt(sq_W_context.sum(axis=1))
#         # col_norms_W_context = T.sqrt(sq_W_context.sum(axis=0))

#         # row_norms_W_target = T.sqrt(sq_W_target.sum(axis=1))
#         # col_norms_W_target = T.sqrt(sq_W_target.sum(axis=0))
        
#         # col_norms_b = T.sqrt(sq_b.sum(axis=0))

        
#         # col_norms_c = T.sqrt(sq_c.sum(axis=0))

#         # rval = OrderedDict([
#         #                     ('W_context_row_norms_min'  , row_norms_W_context.min()),
#         #                     ('W_context_row_norms_mean' , row_norms_W_context.mean()),
#         #                     ('W_context_row_norms_max'  , row_norms_W_context.max()),
#         #                     ('W_context_col_norms_min'  , col_norms_W_context.min()),
#         #                     ('W_context_col_norms_mean' , col_norms_W_context.mean()),
#         #                     ('W_context_col_norms_max'  , col_norms_W_context.max()),

#         #                     ('W_target_row_norms_min'  , row_norms_W_target.min()),
#         #                     ('W_target_row_norms_mean' , row_norms_W_target.mean()),
#         #                     ('W_target_row_norms_max'  , row_norms_W_target.max()),
#         #                     ('W_target_col_norms_min'  , col_norms_W_target.min()),
#         #                     ('W_target_col_norms_mean' , col_norms_W_target.mean()),
#         #                     ('W_target_col_norms_max'  , col_norms_W_target.max()),
                            
#         #                     ('b_col_norms_min'  , col_norms_b.min()),
#         #                     ('b_col_norms_mean' , col_norms_b.mean()),
#         #                     ('b_col_norms_max'  , col_norms_b.max()),

#         #                     ('c_col_norms_min'  , col_norms_c.min()),
#         #                     ('c_col_norms_mean' , col_norms_c.mean()),
#         #                     ('c_col_norms_max'  , col_norms_c.max()),
#         #                     ])

#         rval['nll'] = self.cost_from_X(data)
#         rval['perplexity'] = 10 ** (rval['nll']/np.log(10).astype('float32'))
#         return rval

