import numpy as np
import torch


class InferenceMethod:
    '''Base class for inference method.'''

    def __init__(self, solver):
        '''
        Parameters
        ----------
        '''
        self.solver = solver

    def initialize(self, a):
        '''
        Define initial coefficients.

        Parameters
        ----------

        Returns
        -------

        '''
        raise NotImplementedError

    def grad(self):
        '''
        Compute the gradient step.

        Parameters
        ----------

        Returns
        -------
        '''
        raise NotImplementedError


    def infer(self,dictionary,data,coeff_0=None,use_checknan=False):
        '''
        Infer the coefficients given a dataset and dictionary.

        Parameters
        ----------
        dictionary : array like (n_features,n_basis)

        data : array like (n_samples,n_features)

        coeff_0 : array-like (n_samples,n_basis)
            initial coefficient values
        use_checknan : boolean (1,)
            check for nans in coefficients on each iteration

        Returns
        -------
        coefficients : (n_samples,n_basis)
        '''
        raise NotImplementedError

    @staticmethod
    def checknan(data=torch.tensor(0), name='data'):
        '''
        Check for nan values in data.

        Parameters
        ----------
        data : torch.tensor default=1
            data to check for nans
        name : string
            name to add to error, if one is thrown
        '''
        if torch.isnan(data).any():
            raise ValueError('InferenceMethod error: nan in %s.' % (name))



class LCA(InferenceMethod):
    def __init__(self, n_iter=100, coeff_lr=1e-3, threshold=0.1, stop_early=False, epsilon=1e-2, solver=None, return_all_coefficients='none'):
        '''
        Method implemented according locally competative algorithm (Rozell 2008)
        with the ideal soft thresholding function.

        Parameters
        ----------
        n_iter : scalar (1,) default=100
            number of iterations to run
        coeff_lr : scalar (1,) default=1e-3
            update rate of coefficient dynamics
        threshold : scalar (1,) default=0.1
            threshold for non-linearity
        stop_early : boolean (1,) default=False
            stops dynamics early based on change in coefficents
        epsilon : scalar (1,) default=1e-2
            only used if stop_early True, specifies criteria to stop dynamics
        return_all_coefficients : string (1,) default='none'
            options: ['none','membrane','active']
            returns all coefficients during inference procedure if not equal to 'none'
            if return_all_coefficients=='membrane', membrane potentials (u) returned.
            if return_all_coefficients=='active', active units (a) (output of thresholding function over u) returned.
            user beware: if n_iter is large, setting this parameter to True
            can result in large memory usage/potential exhaustion. This function typically used for
            debugging
        solver : default=None
        '''
        super().__init__(solver)
        self.threshold = threshold
        self.coeff_lr = coeff_lr
        self.stop_early = stop_early
        self.epsilon = epsilon
        self.n_iter = n_iter
        if return_all_coefficients not in ['none','membrane','active']:
            raise ValueError("Invalid input for return_all_coefficients. Valid inputs are: \'none\', \'membrane\', \'active\'.")
        self.return_all_coefficients = return_all_coefficients

    def threshold_nonlinearity(self, u):
        """
        Soft threshhold function according to Rozell 2008

        Parameters
        ----------
        u - torch.tensor (batch_size,n_basis)
            membrane potentials

        Returns
        -------
        a - torch.tensor (batch_size,n_basis)
            activations
        """
        a = (torch.abs(u) - self.threshold).clamp(min=0.)
        a = torch.sign(u)*a
        return a

    def grad(self, b, G, u, a):
        '''
        Compute the gradient step on membrane potentials

        Parameters
        ----------
        b : scalar (batch_size,n_coefficients)
            driver signal for coefficients
        G : scalar (n_coefficients,n_coefficients)
            inhibition matrix
        a : scalar (batch_size,n_coefficients)
            currently active coefficients

        Returns
        -------
        du : scalar (batch_size,n_coefficients)
            grad of membrane potentials
        '''
        du = b-u-(G@a.t()).t()
        return du

    def infer(self, data, dictionary, coeff_0=None,use_checknan=False):
        """
        Infer coefficients using provided dictionary

        Parameters
        ----------
        dictionary : array like (n_features,n_basis)

        data : array like (n_samples,n_features)

        coeff_0 : array-like (n_samples,n_basis)
            initial coefficient values
        use_checknan : boolean (1,) default=False
            check for nans in coefficients on each iteration. Setting this to False
            can speed up inference time

        Returns
        -------
        coefficients : (n_samples,n_basis) OR (n_samples,<=(n_iter+1),n_basis)
           first case occurs if return_all_coefficients == 'none'. if return_all_coefficients != 'none',
           returned shape is second case. Returned dimension < occurs when
           stop_early==True and stopping criteria met.
        """
        batch_size, n_features = data.shape
        n_features, n_basis = dictionary.shape
        device = dictionary.device

        # initialize
        if coeff_0 is not None:
            u = coeff_0.to(device)
        else:
            u = torch.zeros((batch_size, n_basis)).to(device)

        coefficients = torch.zeros((batch_size, 0, n_basis)).to(device)

        b = (dictionary.t()@data.t()).t()
        G = dictionary.t()@dictionary-torch.eye(n_basis).to(device)
        for i in range(self.n_iter):
            # store old membrane potentials to evalute stop early condition
            if self.stop_early:
                old_u = u.clone().detach()

             # check return all
            if self.return_all_coefficients is not 'none':
                if self.return_all_coefficients is 'active':
                    coefficients = torch.concat([coefficients, self.threshold_nonlinearity(u).clone().unsqueeze(1)],dim=1)
                else:
                    coefficients = torch.concat([coefficients, u.clone().unsqueeze(1)],dim=1)

            # compute new
            a = self.threshold_nonlinearity(u)
            du = self.grad(b, G, u, a)
            u = u + self.coeff_lr*du

            # check stopping condition
            if self.stop_early:
                if torch.linalg.norm(old_u - u)/torch.linalg.norm(old_u) < self.epsilon:
                    break

            if use_checknan:
                self.checknan(u, 'coefficients')

        # return active units if return_all_coefficients in ['none','active']
        if self.return_all_coefficients is 'membrane':
            coefficients = torch.concat([coefficients, u.clone().unsqueeze(1)],dim=1)
        else:
            final_coefficients = self.threshold_nonlinearity(u)
            coefficients = torch.concat([coefficients, final_coefficients.clone().unsqueeze(1)],dim=1)

        return coefficients.squeeze()


class Vanilla(InferenceMethod):
    def __init__(self, n_iter=100, coeff_lr=1e-3, sparsity_penalty=0.2, stop_early=False, epsilon=1e-2, solver=None, return_all_coefficients=False):
        '''
        Gradient descent with Euler's method on model in Olhausen & Feild (1997)
        with laplace prior over coefficients (corresponding to l-1 norm penalty).

        Parameters
        ----------
        n_iter : scalar (1,) default=100
            number of iterations to run
        coeff_lr : scalar (1,) default=1e-3
            update rate of coefficient dynamics
        sparsity_penalty : scalar (1,) default=0.2

        stop_early : boolean (1,) default=False
            stops dynamics early based on change in coefficents
        epsilon : scalar (1,) default=1e-2
            only used if stop_early True, specifies criteria to stop dynamics
        return_all_coefficients : string (1,) default=False
            returns all coefficients during inference procedure if True
            user beware: if n_iter is large, setting this parameter to True
            can result in large memory usage/potential exhaustion. This function typically used for
            debugging
        solver : default=None
        '''
        super().__init__(solver)
        self.coeff_lr = coeff_lr
        self.sparsity_penalty = sparsity_penalty
        self.stop_early = stop_early
        self.epsilon = epsilon
        self.n_iter = n_iter
        self.return_all_coefficients = return_all_coefficients

    def grad(self, residual, dictionary, a):
        '''
        Compute the gradient step on coefficients

        Parameters
        ----------
        residual : scalar (batch_size,n_features)
            residual between reconstructed image and original
        dictionary : scalar (n_features,n_coefficients)

        a : scalar (batch_size,n_coefficients)

        Returns
        -------
        da : scalar (batch_size,n_coefficients)
            grad of membrane potentials
        '''
        da = (dictionary.t()@residual.t()).t() - \
            self.sparsity_penalty*torch.sign(a)
        return da


    def infer(self, data, dictionary, coeff_0=None, use_checknan=False):
        """
        Infer coefficients using provided dictionary

        Parameters
        ----------
        dictionary : array like (n_features,n_basis)

        data : array like (n_samples,n_features)

        coeff_0 : array-like (n_samples,n_basis)
            initial coefficient values
        use_checknan : boolean (1,) default=False
            check for nans in coefficients on each iteration. Setting this to False
            can speed up inference time
        Returns
        -------
        coefficients : (n_samples,n_basis) OR (n_samples,<=(n_iter+1),n_basis)
           first case occurs if return_all_coefficients is False. if return_all_coefficients True,
           returned shape is second case. Returned dimension < occurs when
           stop_early==True and stopping criteria met.
        """
        batch_size, n_features = data.shape
        n_features, n_basis = dictionary.shape
        device = dictionary.device

        # initialize
        if coeff_0 is not None:
            a = coeff_0.to(device)
        else:
            a = torch.rand((batch_size, n_basis)).to(device)-0.5

        coefficients = torch.zeros((batch_size, 0, n_basis)).to(device)

        residual = data - (dictionary@a.t()).t()
        for i in range(self.n_iter):

            if self.return_all_coefficients:
                coefficients = torch.concat([coefficients, a.clone().unsqueeze(1)],dim=1)

            if self.stop_early:
                old_a = a.clone().detach()

            da = self.grad(residual, dictionary, a)
            a = a + self.coeff_lr*da

            if self.stop_early:
                if torch.linalg.norm(old_a - a)/torch.linalg.norm(old_a) < self.epsilon:
                    break

            residual = data - (dictionary@a.t()).t()

            if use_checknan:
                self.checknan(a, 'coefficients')

        coefficients = torch.concat([coefficients, a.clone().unsqueeze(1)],dim=1)
        return torch.squeeze(coefficients)


class ISTA(InferenceMethod):
    def __init__(self, n_iter=100, sparsity_penalty=1e-2, stop_early=False,
                 epsilon=1e-2, solver=None, return_all_coefficients=False):
        '''

        Parameters
        ----------
        n_iter : scalar (1,) default=100
            number of iterations to run
        sparsity_penalty : scalar (1,) default=0.2
        stop_early : boolean (1,) default=False
            stops dynamics early based on change in coefficents
        epsilon : scalar (1,) default=1e-2
            only used if stop_early True, specifies criteria to stop dynamics
        return_all_coefficients : string (1,) default=False
            returns all coefficients during inference procedure if True
            user beware: if n_iter is large, setting this parameter to True
            can result in large memory usage/potential exhaustion. This function typically used for
            debugging
        solver : default=None
        '''
        super().__init__(solver)
        self.n_iter = n_iter
        self.sparsity_penalty = sparsity_penalty
        self.stop_early = stop_early
        self.epsilon = epsilon
        self.coefficients = None
        self.return_all_coefficients = return_all_coefficients

    def threshold_nonlinearity(self, u):
        """
        Soft threshhold function according to Rozell 2008

        Parameters
        ----------
        u - torch.tensor (batch_size,n_basis)
            membrane potentials

        Returns
        -------
        a - torch.tensor (batch_size,n_basis)
            activations
        """
        a = (torch.abs(u) - self.threshold).clamp(min=0.)
        a = torch.sign(u)*a
        return a

    def infer(self, data, dictionary,coeff_0=None,use_checknan=False):
        """
        Infer coefficients for each image in data using dictionary elements.
        Uses ISTA (Beck & Taboulle 2009), equations 1.4 and 1.5.

        Parameters
        ----------
        data : array-like (batch_size, n_features)

        dictionary : array-like, (n_features, n_basis)

        coeff_0 : array-like (n_samples,n_basis)
            initial coefficient values
        use_checknan : boolean (1,) default=False
            check for nans in coefficients on each iteration. Setting this to False
            can speed up inference time
        Returns
        -------
        coefficients : (n_samples,n_basis) OR (n_samples,<=(n_iter+1),n_basis)
           first case occurs if return_all_coefficients is False. if return_all_coefficients True,
           returned shape is second case. Returned dimension < occurs when
           stop_early==True and stopping criteria met.
        """
        batch_size = data.shape[0]
        n_basis = dictionary.shape[1]
        device = dictionary.device

        # Calculate stepsize based on largest eigenvalue of
        # dictionary.T @ dictionary.
        lipschitz_constant = torch.linalg.eigvalsh(
            torch.mm(dictionary.T, dictionary))[-1]
        stepsize = 1. / lipschitz_constant
        self.threshold = stepsize * self.sparsity_penalty

        # Initialize coefficients.
        if coeff_0 is not None:
            u = coeff_0.to(device)
        else:
            u = torch.zeros((batch_size, n_basis)).to(device)
        coefficients = torch.zeros((batch_size, 0, n_basis)).to(device)
        self.coefficients = self.threshold_nonlinearity(u)
        residual = torch.mm(dictionary, self.coefficients.T).T - data

        for _ in range(self.n_iter):
            if self.stop_early:
                old_u = u.clone().detach()

            if self.return_all_coefficients:
                coefficients = torch.concat([coefficients, self.threshold_nonlinearity(u).clone().unsqueeze(1)], dim=1)

            u -= stepsize * torch.mm(residual, dictionary)
            self.coefficients = self.threshold_nonlinearity(u)

            if self.stop_early:
                # Stopping condition is function of change of the coefficients.
                a_change = torch.mean(
                    torch.abs(old_u - u) / stepsize)
                if a_change < self.epsilon:
                    break

            residual = torch.mm(dictionary, self.coefficients.T).T - data
            u = self.coefficients

            if use_checknan:
                self.checknan(u, 'coefficients')


        coefficients = torch.concat([coefficients, self.coefficients.clone().unsqueeze(1)], dim=1)
        return torch.squeeze(coefficients)


class LSM(InferenceMethod):
    """
    Infer coefficients for each image in data using elements dictionary.
    Method implemented according to "Group Sparse Coding with a Laplacian Scale Mixture Prior" (P. J. Garrigues & B. A. Olshausen, 2010)
    """

    def __init__(self, n_iter=100, n_iter_LSM=6, beta=0.01, alpha=80.0, sigma=0.005,
                 sparse_threshold=10**-2, solver=None, return_all_coefficients=False):
        '''

        Parameters
        ----------
        n_iter : scalar (1,) default=100
            number of iterations to run for an optimizer
        n_iter_LSM : scalar (1,) default=6
            number of iterations to run the outer loop of  LSM
        beta : scalar (1,) default=0.01
            LSM parameter used to update lambdas
        alpha : scalar (1,) default=80.0
            LSM parameter used to update lambdas
        sigma : scalar (1,) default=0.005
            LSM parameter used to compute the loss function
        sparse_threshold : scalar (1,) default=10**-2
            threshold used to discard smallest coefficients in the final solution
            SM parameter used to compute the loss function
        return_all_coefficients : string (1,) default=False
            returns all coefficients during inference procedure if True
            user beware: if n_iter is large, setting this parameter to True
            can result in large memory usage/potential exhaustion. This function typically used for
            debugging
        solver : default=None
        '''
        super().__init__(solver)
        self.n_iter = n_iter
        self.n_iter_LSM = n_iter_LSM
        self.beta = beta
        self.alpha = alpha
        self.sigma = sigma
        self.sparse_threshold = sparse_threshold
        self.return_all_coefficients = return_all_coefficients

    def lsm_Loss(self, data, dictionary, coefficients, lambdas, sigma):
        """
        Compute LSM loss according to equation (7) in (P. J. Garrigues & B. A. Olshausen, 2010)

        Parameters
        ----------
        data : array-like (batch_size, n_features)
            data to be used in sparse coding
        dictionary : array-like, (n_features, n_basis)
            dictionary to be used
        coefficients : array-like (batch_size, n_basis)
            the current values of coefficients
        lambdas : array-like (batch_size, n_basis)
            the current values of regularization coefficient for all basis
        sigma : scalar (1,) default=0.005
            LSM parameter used to compute the loss functions

        Returns
        -------
        loss : array-like (batch_size, 1)
            loss values for each data sample
        """

        # Compute loss
        mse_loss = (1/(2*(sigma**2)))*torch.pow(torch.norm(data -
                                                           torch.mm(dictionary, coefficients.t()).t(), p=2, dim=1, keepdim=True), 2)
        sparse_loss = torch.sum(lambdas.mul(
            torch.abs(coefficients)), 1, keepdim=True)
        loss = mse_loss + sparse_loss
        return loss

    def infer(self, data, dictionary):
        """
        Infer coefficients for each image in data using dict elements dictionary using Laplacian Scale Mixture (LSM)

        Parameters
        ----------
        data : array-like (batch_size, n_features)
            data to be used in sparse coding
        dictionary : array-like, (n_features, n_basis)
            dictionary to be used to get the coefficients

        Returns
        -------
        coefficients : array-like (batch_size, n_basis)
        """
        # Get input characteristics
        batch_size, n_features = data.shape
        n_features, n_basis = dictionary.shape
        device = dictionary.device

        # Initialize coefficients for the whole batch
        coefficients = torch.zeros(
            batch_size, n_basis, requires_grad=False, device=device)

        for i in range(0, self.n_iter_LSM):

            # Compute the initial values of lambdas
            lambdas = (self.alpha + 1)/(self.beta +
                                        torch.abs(coefficients)).to(device)

            # Set coefficients to zero before doing repeating the inference with new lambdas
            coefficients = torch.zeros(
                batch_size, n_basis, requires_grad=True, device=device)

            # Set up optimizer
            optimizer = torch.optim.Adam([coefficients])

            # Internal loop to infer the coefficients with the current lambdas
            for t in range(0, self.n_iter):

                # compute LSM loss for the current iteration
                loss = self.lsm_Loss(data=data,
                                     dictionary=dictionary,
                                     coefficients=coefficients,
                                     lambdas=lambdas,
                                     sigma=self.sigma
                                     )

                optimizer.zero_grad()

                # Backward pass: compute gradient of the loss with respect to model parameters
                loss.backward(torch.ones((batch_size, 1),
                              device=device), retain_graph=True)

                # Calling the step function on an Optimizer makes an update to its parameters
                optimizer.step()

        # Sparsify the final solution by discarding the small coefficients
        coefficients.data[torch.abs(coefficients.data)
                          < self.sparse_threshold] = 0

        return coefficients.detach()



class PyTorchOptimizer(InferenceMethod):
    """
    Infer coefficients using provided loss functional and optimizer
    """


    def __init__(self,optimizer_f,loss_f,n_iter=100,solver=None):
        '''

        Parameters
        ----------
        optimizer : pytorch optimizer function handle

        loss_f : function handle
            must have parameters:
                 (data, dictionary, coefficients)
            where data is of size (batch_size,n_features)
            and loss_f must return tensor of size (batch_size,)
        n_iter : scalar (1,) default=100
          number of iterations to run for an optimizer
        solver : default=None
        '''
        super().__init__(solver)
        self.optimizer_f = optimizer_f
        self.loss_f = loss_f
        self.n_iter = n_iter


    def infer(self, data, dictionary, coeff_0=None):
        """
        Infer coefficients for each image in data using dict elements dictionary using Laplacian Scale Mixture (LSM)

        Parameters
        ----------
        data : array-like (batch_size, n_features)
          data to be used in sparse coding

        dictionary : array-like, (n_features, n_basis)
          dictionary to be used to get the coefficients

        Returns
        -------
        coefficients : array-like (batch_size, n_basis)
        """
        # Get input characteristics
        batch_size, n_features = data.shape
        n_features, n_basis = dictionary.shape
        device = dictionary.device

        # Initialize coefficients for the whole batch

        # initialize
        if coeff_0 is not None:
            coefficients = coeff_0.requires_grad_(True)
        else:
            coefficients = torch.zeros((batch_size, n_basis),requires_grad=True,device=device)

        optimizer = self.optimizer_f([coefficients])

        for i in range(self.n_iter):

            # compute LSM loss for the current iteration
            loss = self.loss_f(data=data,
                               dictionary=dictionary,
                               coefficients=coefficients
            )

            optimizer.zero_grad()

            # Backward pass: compute gradient of the loss with respect to model parameters
            loss.backward(torch.ones((batch_size,),device=device))

            # Calling the step function on an Optimizer makes an update to its parameters
            optimizer.step()

        return coefficients.detach()



class IHT(InferenceMethod):
    """
    Infer coefficients for each image in data using elements dictionary.
    Method description can be traced to "Iterative Hard Thresholding for Compressed Sensing" (T. Blumensath & M. E. Davies, 2009)
    """

    def __init__(self, sparsity, n_iter=10, solver=None, return_all_coefficients=False):
        '''

        Parameters
        ----------
        sparsity : scalar (1,)
            sparsity of the solution        
        n_iter : scalar (1,) default=100
            number of iterations to run for an inference method
        return_all_coefficients : string (1,) default=False
            returns all coefficients during inference procedure if True
            user beware: if n_iter is large, setting this parameter to True
            can result in large memory usage/potential exhaustion. This function typically used for
            debugging
        solver : default=None
        '''
        super().__init__(solver)
        self.n_iter = n_iter
        self.sparsity = sparsity
        self.return_all_coefficients = return_all_coefficients



    def infer(self, data, dictionary):
        """
        Infer coefficients for each image in data using dict elements dictionary using Iterative Hard Thresholding (IHT)

        Parameters
        ----------
        data : array-like (batch_size, n_features)
            data to be used in sparse coding
        dictionary : array-like, (n_features, n_basis)
            dictionary to be used to get the coefficients

        Returns
        -------
        coefficients : array-like (batch_size, n_basis)
        """
        # Get input characteristics
        batch_size, n_features = data.shape
        n_features, n_basis = dictionary.shape
        device = dictionary.device

        # Define signal sparsity
        K = np.ceil(self.sparsity*n_basis).astype(int)

        # Initialize coefficients for the whole batch
        coefficients = torch.zeros(
            batch_size, n_basis, requires_grad=False, device=device)

        # For each sample in the batch
        for i in range(0, batch_size):
            # Pick ith sample 
            y = torch.clone(data[i:i+1,:])
            
            # Initialize coefficients for ith sample 
            coeff = torch.zeros(
                1, n_basis, requires_grad=False, device=device)
            
            
            for t in range(0, self.n_iter):    
                
                # Update coefficients 
                temp = coeff + torch.mm((y-torch.mm(dictionary, coeff.t()).t()),dictionary)

                # Apply kWTA nonlinearity
                sort, indices = torch.sort(torch.abs(temp), dim=1, descending=True)
                coeff = torch.zeros(1, n_basis, device=device)
                coeff[0,indices[0,0:K]]=temp[0,indices[0,0:K]]   
                
                
            coefficients[i,:] = coeff


        return coefficients.detach()


 
class MP(InferenceMethod):
    """
    Infer coefficients for each image in data using elements dictionary.
    Method description can be traced to "Matching Pursuits with Time-Frequency Dictionaries" (S. G. Mallat & Z. Zhang, 1993)
    """

    def __init__(self, sparsity, solver=None, return_all_coefficients=False):
        '''

        Parameters
        ----------
        sparsity : scalar (1,)
            sparsity of the solution        
        return_all_coefficients : string (1,) default=False
            returns all coefficients during inference procedure if True
            user beware: if n_iter is large, setting this parameter to True
            can result in large memory usage/potential exhaustion. This function typically used for
            debugging
        solver : default=None
        '''
        super().__init__(solver)
        self.sparsity = sparsity
        self.return_all_coefficients = return_all_coefficients



    def infer(self, data, dictionary):
        """
        Infer coefficients for each image in data using dict elements dictionary using Matching Pursuit (MP)

        Parameters
        ----------
        data : array-like (batch_size, n_features)
            data to be used in sparse coding
        dictionary : array-like, (n_features, n_basis)
            dictionary to be used to get the coefficients

        Returns
        -------
        coefficients : array-like (batch_size, n_basis)
        """
        # Get input characteristics
        batch_size, n_features = data.shape
        n_features, n_basis = dictionary.shape
        device = dictionary.device

        # Define signal sparsity
        K = np.ceil(self.sparsity*n_basis).astype(int)
        
        # Get dictionary norms in case atoms are not normalized 
        dictionary_norms = torch.norm(dictionary, p=2, dim=0,keepdim=True)        
        
        # Initialize coefficients for the whole batch
        coefficients = torch.zeros(
            batch_size, n_basis, requires_grad=False, device=device)
        
        # For each sample in the batch
        for i in range(0, batch_size):
            # Pick ith sample 
            y = torch.clone(data[i:i+1,:])
            
            # Initialize coefficients for ith sample 
            coeff = torch.zeros(
                1, n_basis, requires_grad=False, device=device)
                
            for t in range(0, K):    
                
                # Compute inner product
                dp = torch.mm(y,dictionary)
                
                # Get the location of the most activated  atom
                ind = torch.argmax(torch.abs(dp)/dictionary_norms)

                # Add new value of the most activated atom
                coeff[0,ind] = dp[0,ind]

                # Explain away the chosen atom
                y = y - dp[0,ind]*dictionary[:,ind:ind+1].t()
                
            coefficients[i,:] = coeff

        return coefficients.detach()





class OMP(InferenceMethod):
    """
    Infer coefficients for each image in data using elements dictionary.
    Method description can be traced to "Orthogonal Matching Pursuit: Recursive Function Approximation with Application to Wavelet Decomposition" (Y. Pati & R. Rezaiifar & P. Krishnaprasad, 1993)
    """

    def __init__(self, sparsity, solver=None, return_all_coefficients=False):
        '''

        Parameters
        ----------
        sparsity : scalar (1,)
            sparsity of the solution        
        return_all_coefficients : string (1,) default=False
            returns all coefficients during inference procedure if True
            user beware: if n_iter is large, setting this parameter to True
            can result in large memory usage/potential exhaustion. This function typically used for
            debugging
        solver : default=None
        '''
        super().__init__(solver)
        self.sparsity = sparsity
        self.return_all_coefficients = return_all_coefficients



    def infer(self, data, dictionary):
        """
        Infer coefficients for each image in data using dict elements dictionary using Orthogonal Matching Pursuit (OMP)

        Parameters
        ----------
        data : array-like (batch_size, n_features)
            data to be used in sparse coding
        dictionary : array-like, (n_features, n_basis)
            dictionary to be used to get the coefficients

        Returns
        -------
        coefficients : array-like (batch_size, n_basis)
        """
        # Get input characteristics
        batch_size, n_features = data.shape
        n_features, n_basis = dictionary.shape
        device = dictionary.device

        # Define signal sparsity
        K = np.ceil(self.sparsity*n_basis).astype(int)
        
        # Get dictionary norms in case atoms are not normalized 
        dictionary_norms = torch.norm(dictionary, p=2, dim=0,keepdim=True)        
        
        # Initialize coefficients for the whole batch
        coefficients = torch.zeros(
            batch_size, n_basis, requires_grad=False, device=device)
        
        # For each sample in the batch
        for i in range(0, batch_size):
            # Pick ith sample 
            y = torch.clone(data[i:i+1,:])
            
            # Initialize residual
            y_res = torch.clone(y)           

            #Store indices of the chosen atoms
            indices = torch.empty((0), dtype=torch.int64, device=device)
            
            # Initialize coefficients for ith sample 
            coeff = torch.zeros(
                1, n_basis, requires_grad=False, device=device)
                
            for t in range(0, K):    
                
                # Compute inner product
                dp = torch.mm(y_res,dictionary)
                
                # Get the location of the most activated  atom
                ind = torch.argmax(torch.abs(dp)/dictionary_norms)

                #Add the chosen atom
                indices = torch.cat((indices, ind.unsqueeze(0)), 0)
                
                # Solve MSE minimization using the subset of the chosen atoms
                coeff[0,indices] = torch.squeeze(torch.mm(torch.linalg.pinv(dictionary[:,indices]), y.t()))

                # Compute the residual by explaining away the current coefficients
                y_res = y - torch.mm(dictionary, coeff.t()).t()
                
            coefficients[i,:] = coeff

        return coefficients.detach()

